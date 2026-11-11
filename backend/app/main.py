import logging
import hashlib
import json
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from .config import settings
from .db import engine, init_db
from .models import Document, Chunk, Message
from .api import documents, conversations, chat, feedback, settings as settings_api, evaluation
from .rag.ingest import scan_documents, state, validate_vector_index

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    init_db()
    with Session(engine) as session:
        # A previous process may have been closed during generation/indexing.
        for message in session.exec(select(Message).where(Message.status == 'generating')).all():
            message.status = 'stopped'
            session.add(message)
        for doc in session.exec(select(Document).where(Document.status == 'processing')).all():
            doc.status, doc.error = 'failed', '上次入库被中断，请重新建立索引。'
            session.add(doc)
        session.commit()
    scan_documents()
    await asyncio.to_thread(validate_vector_index)
    from .rag.retrieve import warmup
    # Readiness is announced after existing indexes and the local model are warm.
    await asyncio.to_thread(warmup)
    yield


app = FastAPI(title='MedRAG', version='1.0.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173', 'http://127.0.0.1:8000'],
                   allow_methods=['GET', 'POST', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'Accept'])


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'HTTP_ERROR', 'message': str(exc.detail)}
    return JSONResponse({'error': detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({'error': {'code': 'INVALID_REQUEST', 'message': '请求参数不正确，请检查输入。'}}, status_code=400)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    logging.exception('Unhandled application error')
    return JSONResponse({'error': {'code': 'INTERNAL_ERROR', 'message': '本地服务发生错误，请查看启动窗口中的日志。'}}, status_code=500)


for router in (documents.router, conversations.router, chat.router, feedback.router, settings_api.router, evaluation.router):
    app.include_router(router)


@app.get('/api/health')
def health():
    with Session(engine) as session:
        docs = session.exec(select(Document)).all()
        chunks = session.exec(select(Chunk.id)).all()
    project_id = hashlib.sha256(Path(__file__).resolve().parents[2].as_posix().lower().encode()).hexdigest()[:16]
    return {'status': 'ok', 'service': 'medrag', 'project_id': project_id,
            'version': '1.0.0', 'llm_model': settings.llm_model,
            'library_revision': hashlib.sha256(json.dumps(
                sorted((doc.id, doc.file_hash, doc.status, doc.num_chunks, doc.indexed_backend) for doc in docs)
            ).encode()).hexdigest()[:20],
            'llm_configured': bool(settings.llm_api_key), 'answer_mode': 'deepseek' if settings.llm_api_key else 'extractive',
            'embedding_model': settings.embedding_model if settings.embedding_backend == 'bge' else 'BM25（轻量模式）',
            'embedding_backend': settings.embedding_backend, 'num_documents': len(docs), 'num_chunks': len(chunks),
            'num_ready': sum(d.status == 'ready' for d in docs), 'task': dict(state)}


DIST = Path(__file__).resolve().parents[2] / 'frontend/dist'
if DIST.is_dir():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')

    @app.get('/')
    def frontend():
        return FileResponse(DIST / 'index.html')
