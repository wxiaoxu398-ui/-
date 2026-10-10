import asyncio
import os
import tempfile
import threading
import uuid
import html
from urllib.parse import urlencode
from pathlib import Path
import pymupdf as fitz
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response
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
    _, path = original_path(document_id, hash, session)
    return FileResponse(path, media_type='application/pdf', filename=path.name, content_disposition_type='inline')


def original_path(document_id, hash, session):
    doc = session.get(Document, document_id)
    if not doc:
        fail(404, 'DOC_NOT_FOUND', '文档不存在。')
    path = Path(doc.file_path).resolve()
    if path.parent != settings.books_dir.resolve() or not path.is_file():
        fail(404, 'PDF_NOT_FOUND', '原始 PDF 已移走，无法打开。')
    if hash and file_hash(path) != hash:
        fail(409, 'PDF_CHANGED', '原始 PDF 已替换，历史引用页码无法核验；保存的原文摘录仍可查看。')
    return doc, path


@router.get('/{document_id}/page/image')
def original_page_image(document_id: int, page: int, hash: str | None = None, session: Session = Depends(get_session)):
    _, path = original_path(document_id, hash, session)
    with fitz.open(path) as pdf:
        if not 1 <= page <= len(pdf):
            fail(400, 'INVALID_PAGE', '这个物理页码不在 PDF 范围内。')
        original = pdf[page - 1]
        scale = min(2, 2400 / max(original.rect.width, original.rect.height))
        image = original.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
    return Response(image, media_type='image/png', headers={'Cache-Control': 'private, no-store'})


@router.get('/{document_id}/page', response_class=HTMLResponse)
def original_page(document_id: int, page: int, hash: str | None = None, session: Session = Depends(get_session)):
    doc, path = original_path(document_id, hash, session)
    with fitz.open(path) as pdf:
        total = len(pdf)
    if not 1 <= page <= total:
        fail(400, 'INVALID_PAGE', '这个物理页码不在 PDF 范围内。')
    base = f'/api/documents/{document_id}'
    fingerprint = {'hash': hash} if hash else {}
    def page_url(number):
        return html.escape(base + '/page?' + urlencode(dict(fingerprint, page=number)), quote=True)
    image_url = html.escape(base + '/page/image?' + urlencode(dict(fingerprint, page=page)), quote=True)
    pdf_url = html.escape(base + '/file?' + urlencode(fingerprint) + f'#page={page}', quote=True)
    previous = f'<a href="{page_url(page - 1)}">上一页</a>' if page > 1 else '<span>上一页</span>'
    following = f'<a href="{page_url(page + 1)}">下一页</a>' if page < total else '<span>下一页</span>'
    title = html.escape(doc.title)
    provenance = html.escape(f'{doc.source} · {doc.license}')
    # Render the original physical page explicitly: native PDF viewers may ignore #page.
    return HTMLResponse(f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · PDF 第 {page} 页</title>
<style>body{{margin:0;background:#f3f4f1;color:#223c34;font:15px system-ui,sans-serif}}
header{{position:sticky;top:0;background:#fffef9;padding:16px 24px;border-bottom:1px solid #dce2db;display:flex;gap:16px;align-items:center;flex-wrap:wrap}}
h1{{font-size:17px;margin:0;flex:1}}nav{{display:flex;gap:16px;align-items:center}}a{{color:#2d6b50}}nav span{{color:#929b95}}
main{{max-width:1100px;margin:24px auto;padding:0 16px}}img{{display:block;width:100%;height:auto;background:white;box-shadow:0 2px 12px #0001}}
footer{{padding:20px 0;color:#53655c;font-size:13px;overflow-wrap:anywhere}}</style></head><body>
<header><h1>{title}</h1><strong>PDF 第 {page} 页 / {total} 页</strong><nav>{previous}{following}
<a href="{pdf_url}" target="_blank" rel="noopener">打开完整 PDF</a></nav></header>
<main><img src="{image_url}" alt="{title}，PDF 物理页 {page}"><footer>{provenance}<br>
本工具仅供医学学习参考，不构成医疗建议。页码为 PDF 物理页，可能与书内印刷页码不同。</footer></main></body></html>''')
