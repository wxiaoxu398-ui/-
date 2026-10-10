import hashlib
import json
import logging
import threading
from pathlib import Path
import pymupdf as fitz
from sqlalchemy import delete
from sqlmodel import Session, select
from ..config import settings
from ..db import engine
from ..models import Document, Chunk
from .splitter import split_pages

logger = logging.getLogger(__name__)
index_lock = threading.Lock()
state = {'status': 'idle', 'processed': 0, 'total': 0, 'current': '', 'error': '', 'task_id': ''}
seen_files = {}


def file_hash(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def scan_documents():
    settings.books_dir.mkdir(parents=True, exist_ok=True)
    root = settings.books_dir.resolve()
    paths = {p.name: p for p in root.iterdir() if p.suffix.lower() == '.pdf' and p.is_file() and p.resolve().parent == root}
    changed = False
    with Session(engine) as session:
        known = {doc.file_name: doc for doc in session.exec(select(Document)).all()}
        for name, path in paths.items():
            if name in known:
                doc = known[name]
                signature = (path.stat().st_size, path.stat().st_mtime_ns)
                if doc.status == 'ready' and seen_files.get(name) != signature and file_hash(path) != doc.file_hash:
                    doc.status, doc.error = 'pending', '原始 PDF 已更新，请重新建立索引。'
                    session.add(doc)
                    changed = True
                elif doc.error.startswith('原始 PDF 已移走'):
                    doc.status, doc.error = 'pending', ''
                    session.add(doc)
                    changed = True
                seen_files[name] = signature
                continue
            try:
                with fitz.open(path) as pdf:
                    meta = pdf.metadata or {}
                    doc = Document(title=meta.get('title') or path.stem, author=meta.get('author') or '',
                                   file_name=name, file_path=str(path), num_pages=len(pdf))
            except Exception:
                doc = Document(title=path.stem, file_name=name, file_path=str(path), status='failed', error='PDF 无法读取，请检查文件。')
            sidecar = path.with_suffix('.json')
            if sidecar.is_file():
                try:
                    data = json.loads(sidecar.read_text(encoding='utf-8'))
                    for key in ('title', 'author', 'source', 'license', 'source_url'):
                        if isinstance(data.get(key), str):
                            setattr(doc, key, data[key][:1000])
                except (ValueError, OSError):
                    pass
            session.add(doc)
            changed = True
        for name, doc in known.items():
            if name not in paths:
                changed = changed or doc.status != 'failed'
                doc.status, doc.error = 'failed', '原始 PDF 已移走；历史引文仍保留。'
                session.add(doc)
        session.commit()
    if changed:
        from .retrieve import invalidate
        invalidate()


def ingest_all(rebuild=False):
    from .embedder import collection, embed, delete_document
    failed = []
    try:
        scan_documents()
        with Session(engine) as session:
            ids = [d.id for d in session.exec(select(Document)).all() if Path(d.file_path).is_file()]
            state.update(status='processing', total=len(ids), processed=0, error='')
            for document_id in ids:
                doc = session.get(Document, document_id)
                path = Path(doc.file_path)
                state['current'] = doc.title
                try:
                    digest = file_hash(path)
                    if not rebuild and doc.status == 'ready' and doc.file_hash == digest and doc.indexed_backend == settings.embedding_backend:
                        state['processed'] += 1
                        continue
                    doc.status, doc.error = 'processing', ''
                    session.add(doc)
                    session.commit()
                    with fitz.open(path) as pdf:
                        if pdf.needs_pass:
                            raise ValueError('PDF 受密码保护，请导入可读取的版本。')
                        doc.num_pages = len(pdf)
                        pages = [page.get_text(sort=True) for page in pdf]
                    pieces = list(split_pages(pages))
                    if not pieces or sum(len(p['content']) for p in pieces) < 20:
                        raise ValueError('未提取到可检索文字，可能是扫描版 PDF；请先 OCR 或使用文字版。')
                    delete_document(document_id)
                    session.exec(delete(Chunk).where(Chunk.document_id == document_id))
                    chunks = [Chunk(document_id=document_id, chunk_index=i, **piece) for i, piece in enumerate(pieces)]
                    session.add_all(chunks)
                    session.flush()
                    if settings.embedding_backend == 'bge':
                        for start in range(0, len(chunks), 32):
                            batch = chunks[start:start + 32]
                            collection().upsert(ids=[f'chunk_{c.id}' for c in batch],
                                embeddings=embed([c.content for c in batch]), documents=[c.content for c in batch],
                                metadatas=[{'chunk_id': c.id, 'document_id': document_id, 'page_start': c.page_start, 'page_end': c.page_end} for c in batch])
                    elif settings.embedding_backend != 'lexical':
                        raise ValueError('EMBEDDING_BACKEND 必须为 bge 或 lexical。')
                    # A concurrent file replacement must never receive the previous file's index.
                    if file_hash(path) != digest:
                        raise ValueError('入库时 PDF 发生变化，请重新建立索引。')
                    doc.status, doc.num_chunks, doc.file_hash = 'ready', len(chunks), digest
                    doc.indexed_backend = settings.embedding_backend
                    session.add(doc)
                    session.commit()
                except Exception as exc:
                    logger.exception('Document indexing failed')
                    session.rollback()
                    doc = session.get(Document, document_id)
                    doc.status = 'failed'
                    doc.error = str(exc)[:400]
                    session.add(doc)
                    session.commit()
                    failed.append(doc.title)
                state['processed'] += 1
        from .retrieve import invalidate
        invalidate()
        state.update(status='failed' if failed else 'complete', current='', error='部分文档入库失败：' + '、'.join(failed) if failed else '')
    except Exception:
        logger.exception('Index task failed')
        state.update(status='failed', current='', error='索引任务失败，请查看本地日志。')
    finally:
        if index_lock.locked():
            index_lock.release()


if __name__ == '__main__':
    from ..db import init_db
    init_db()
    index_lock.acquire()
    ingest_all(rebuild=True)
    print(json.dumps(state, ensure_ascii=False))
