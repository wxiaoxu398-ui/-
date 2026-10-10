import asyncio
import json
import time
from fastapi import APIRouter, Request
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse
from ..config import settings
from ..db import engine
from ..models import Conversation, Message, Document, now
from ..schemas import ChatRequest
from ..errors import fail
from ..rag.ingest import index_lock
from ..rag.retrieve import hybrid_search
from ..rag.generate import generate, clinical_request
from ..rag.prompts import REFUSAL

router = APIRouter(prefix='/api/chat', tags=['chat'])


def event(name, data):
    return {'event': name, 'data': json.dumps(data, ensure_ascii=False)}


@router.post('/stream')
async def chat(payload: ChatRequest, request: Request):
    question = payload.question.strip()
    if not question:
        fail(400, 'EMPTY_QUESTION', '请输入问题。')
    if index_lock.locked():
        fail(409, 'INDEX_BUSY', '索引正在重建，请完成后提问。')
    with Session(engine) as session:
        if payload.document_ids is not None:
            if len(payload.document_ids) > 100:
                fail(400, 'INVALID_DOCUMENTS', '筛选文档过多。')
            valid = {d.id for d in session.exec(select(Document)).all()}
            if any(i not in valid for i in payload.document_ids):
                fail(400, 'INVALID_DOCUMENTS', '筛选中存在无效文档。')
        conversation = session.get(Conversation, payload.conversation_id) if payload.conversation_id else None
        if payload.conversation_id and not conversation:
            fail(404, 'CONVERSATION_NOT_FOUND', '会话不存在。')
        if conversation:
            rows = session.exec(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id.desc()).limit(6)).all()
            if any(row.status == 'generating' for row in rows):
                fail(409, 'CHAT_BUSY', '当前会话正在生成回答。')
            history = [{'role': row.role, 'content': row.content} for row in reversed(rows) if row.status == 'complete']
        else:
            conversation = Conversation(title=question[:30])
            session.add(conversation)
            session.flush()
            history = []
        conversation.updated_at = now()
        session.add(conversation)
        session.add(Message(conversation_id=conversation.id, role='user', content=question))
        assistant = Message(conversation_id=conversation.id, role='assistant', status='generating')
        session.add(assistant)
        session.commit()
        cid, mid = conversation.id, assistant.id

    async def stream():
        answer, sources, metrics = '', [], {}
        status, finish = 'complete', 'stop'
        started, first = time.perf_counter(), None
        try:
            mode = 'deepseek' if settings.llm_api_key else 'extractive'
            yield event('meta', {'conversation_id': cid, 'message_id': mid, 'mode': mode})
            sources = [] if clinical_request(question) else await asyncio.to_thread(hybrid_search, question, payload.top_k, payload.document_ids, history)
            if mode == 'extractive':
                sources = sources[:3]
            yield event('sources', {'sources': sources})
            async for block in generate(question, sources, history, metrics):
                if await request.is_disconnected():
                    status, finish = 'stopped', 'cancelled'
                    break
                first = first or time.perf_counter()
                answer += block
                yield event('delta', {'content': block})
            if answer.strip() == REFUSAL:
                sources = []
                yield event('sources', {'sources': []})
                finish = 'refused'
            elif mode == 'deepseek':
                import re
                used = {int(x) for x in re.findall(r'\[(\d+)\]', answer)}
                sources = [source for source in sources if source['index'] in used]
                yield event('sources', {'sources': sources})
        except asyncio.CancelledError:
            status, finish = 'stopped', 'cancelled'
            raise
        except Exception as exc:
            status, finish = 'error', 'error'
            if not answer:
                sources = []
            detail = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else '检索或生成失败，请检查本地日志与索引状态。'
            yield event('error', {'code': 'LLM_ERROR', 'message': detail})
        finally:
            metrics.update(first_token_ms=round((first - started) * 1000) if first else None,
                           duration_ms=round((time.perf_counter() - started) * 1000))
            # finally executes on AbortController disconnect, keeping partial output and status.
            with Session(engine) as session:
                saved = session.get(Message, mid)
                if saved:
                    saved.content, saved.sources_json, saved.status = answer, json.dumps(sources, ensure_ascii=False), status
                    saved.metrics_json = json.dumps(metrics, ensure_ascii=False)
                    session.add(saved)
                    session.commit()
        yield event('done', {'message_id': mid, 'finish_reason': finish,
                            'first_token_ms': round((first - started) * 1000) if first else None,
                            'duration_ms': round((time.perf_counter() - started) * 1000), 'metrics': metrics})

    return EventSourceResponse(stream(), ping=10, headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
