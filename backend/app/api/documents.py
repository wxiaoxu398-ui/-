import asyncio
import os
import tempfile
import threading
import uuid
from pathlib import Path
import pymupdf as fitz
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import FileResponse
from sqlmodel import Session, select
from ..config import settings
from ..db import get_session
from ..models import Document
from ..schemas import IngestRequest, MetadataRequest
from ..errors import fail
from ..rag.ingest import scan_documents, ingest_all, index_lock, state, file_hash

router = APIRouter(prefix='/api/documents', tags=['documents'])


def public(doc):
    return doc.model_dump(exclude={'file_path', 'file_hash'})


@router.get('')
def documents(session: Session = Depends(get_session)):
    if not index_lock.locked():
        scan_documents()
    items = [public(doc) for doc in session.exec(select(Document).order_by(Document.id)).all()]
    return {'items': items, 'total': len(items), 'task': dict(state)}


@router.post('/ingest')
def start_ingest(payload: IngestRequest):
    if not index_lock.acquire(blocking=False):
        fail(409, 'INDEX_BUSY', '已有索引任务正在执行。')
    task_id = 'ingest-' + uuid.uuid4().hex[:12]
    state.update(status='processing', task_id=task_id, processed=0, total=0, current='', error='')
    threading.Thread(target=ingest_all, args=(payload.rebuild,), daemon=True).start()
    return {'task_id': task_id, 'status': 'started', 'message': '索引任务已启动。'}


@router.post('/upload')
async def upload(file: UploadFile = File(...)):
    if index_lock.locked():
        fail(409, 'INDEX_BUSY', '入库期间请稍后导入 PDF。')
    name = Path((file.filename or '').replace('\\', '/')).name
    if not name.lower().endswith('.pdf') or not name or len(name) > 200 or any(c in name for c in '<>:"|?*'):
        fail(400, 'INVALID_FILE', '请选择有效的 PDF 文件。')
    settings.books_dir.mkdir(parents=True, exist_ok=True)
    target = settings.books_dir / name
    if target.exists():
        fail(409, 'FILE_EXISTS', '同名 PDF 已存在，请重命名后导入。')
    descriptor, temp_name = tempfile.mkstemp(dir=settings.books_dir, suffix='.upload')
    os.close(descriptor)
    size = 0
    try:
        with open(temp_name, 'wb') as handle:
            while block := await file.read(1024 * 1024):
                size += len(block)
                if size > 200 * 1024 * 1024:
                    fail(400, 'FILE_TOO_LARGE', '单个 PDF 请控制在 200 MB 以内。')
                handle.write(block)
        try:
            with fitz.open(stream=Path(temp_name).read_bytes(), filetype='pdf') as pdf:
                if pdf.needs_pass or len(pdf) == 0:
                    fail(400, 'INVALID_PDF', 'PDF 为空或受密码保护。')
        except fitz.FileDataError:
            fail(400, 'INVALID_PDF', '无法读取这个 PDF。')
        # Reserve destination exclusively, avoiding duplicate concurrent uploads.
        try:
            with target.open('xb') as dest, open(temp_name, 'rb') as src:
                import shutil
                shutil.copyfileobj(src, dest)
        except FileExistsError:
            fail(409, 'FILE_EXISTS', '同名 PDF 已存在。')
        await asyncio.to_thread(scan_documents)
        return {'success': True, 'file_name': name, 'message': '已导入，请补充来源与许可证后建立索引。'}
    finally:
        Path(temp_name).unlink(missing_ok=True)
        await file.close()


@router.get('/{document_id}')
def document(document_id: int, session: Session = Depends(get_session)):
    doc = session.get(Document, document_id)
    if not doc:
        fail(404, 'DOC_NOT_FOUND', '文档不存在。')
    return public(doc)


@router.patch('/{document_id}')
def metadata(document_id: int, payload: MetadataRequest, session: Session = Depends(get_session)):
    if index_lock.locked():
        fail(409, 'INDEX_BUSY', '入库期间请稍后编辑信息。')
    doc = session.get(Document, document_id)
    if not doc:
        fail(404, 'DOC_NOT_FOUND', '文档不存在。')
    for key, value in payload.model_dump().items():
        setattr(doc, key, value.strip())
    session.add(doc)
    session.commit()
    from ..rag.retrieve import invalidate
    invalidate()
    return public(doc)


@router.api_route('/{document_id}/file', methods=['GET', 'HEAD'])
def original_file(document_id: int, hash: str | None = None, session: Session = Depends(get_session)):
    doc = session.get(Document, document_id)
    if not doc:
        fail(404, 'DOC_NOT_FOUND', '文档不存在。')
    path = Path(doc.file_path).resolve()
    if path.parent != settings.books_dir.resolve() or not path.is_file():
        fail(404, 'PDF_NOT_FOUND', '原始 PDF 已移走，无法打开。')
    if hash and file_hash(path) != hash:
        fail(409, 'PDF_CHANGED', '原始 PDF 已替换，历史引用页码无法核验；保存的原文摘录仍可查看。')
    return FileResponse(path, media_type='application/pdf', filename=path.name, content_disposition_type='inline')
