from fastapi import APIRouter
from pydantic import BaseModel, Field
from dotenv import set_key
from ..config import settings, ROOT
from ..errors import fail
from ..rag.ingest import index_lock
from ..db import engine
from ..models import Document, Message
from sqlmodel import Session, select

router = APIRouter(prefix='/api/settings', tags=['settings'])


class SettingsRequest(BaseModel):
    api_key: str = Field(default='', max_length=500)
    clear_api_key: bool = False
    llm_model: str = Field(default='deepseek-chat', min_length=1, max_length=100)
    embedding_backend: str = 'bge'
    similarity_threshold: float = Field(default=0.35, ge=0.1, le=0.95)


@router.get('')
def get_settings():
    return {'llm_configured': bool(settings.llm_api_key), 'llm_model': settings.llm_model,
            'embedding_backend': settings.embedding_backend, 'similarity_threshold': settings.similarity_threshold}


@router.post('')
def save_settings(payload: SettingsRequest):
    if index_lock.locked():
        fail(409, 'INDEX_BUSY', '请等入库完成后修改设置。')
    if payload.embedding_backend not in ('bge', 'lexical'):
        fail(400, 'INVALID_BACKEND', '检索模式无效。')
    with Session(engine) as session:
        if session.exec(select(Message).where(Message.status == 'generating')).first():
            fail(409, 'CHAT_BUSY', '请先停止生成，再修改设置。')
        if payload.embedding_backend != settings.embedding_backend:
            for doc in session.exec(select(Document).where(Document.status == 'ready')).all():
                doc.status, doc.error = 'pending', '检索模式已改变，请重新建立索引。'
                session.add(doc)
            session.commit()
    env = ROOT / '.env'
    values = {'LLM_MODEL': payload.llm_model.strip(), 'EMBEDDING_BACKEND': payload.embedding_backend,
              'SIMILARITY_THRESHOLD': str(payload.similarity_threshold)}
    if payload.clear_api_key:
        values['LLM_API_KEY'] = ''
    elif payload.api_key.strip():
        values['LLM_API_KEY'] = payload.api_key.strip()
    for key, value in values.items():
        set_key(str(env), key, value)
        setattr(settings, key.lower(), float(value) if key == 'SIMILARITY_THRESHOLD' else value)
    from ..rag.retrieve import invalidate
    invalidate()
    return {'success': True, **get_settings()}
