import asyncio
import json
import time
from pathlib import Path
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from ..errors import fail
from ..config import settings
from ..rag.ingest import index_lock
from ..rag.retrieve import hybrid_search
from ..rag.generate import generate
from ..rag.prompts import REFUSAL

router = APIRouter(prefix='/api/evaluation', tags=['evaluation'])
QUESTIONS = Path(__file__).resolve().parents[3] / 'eval/questions.json'


class EvaluationRequest(BaseModel):
    question_ids: list[int] = Field(default_factory=list)
    document_ids: list[int] | None = None


@router.get('/questions')
def questions():
    return {'items': json.loads(QUESTIONS.read_text(encoding='utf-8'))}


@router.post('/run')
async def run(payload: EvaluationRequest, request: Request):
    if index_lock.locked():
        fail(409, 'INDEX_BUSY', '入库完成后才能评估。')
    items = [q for q in questions()['items'] if not payload.question_ids or q['id'] in payload.question_ids]

    async def stream():
        for item in items:
            if await request.is_disconnected():
                return
            started, first = time.perf_counter(), None
            answer, sources, error, metrics = '', [], '', {}
            try:
                sources = await asyncio.to_thread(hybrid_search, item['question'], 6, payload.document_ids)
                if not settings.llm_api_key:
                    sources = sources[:3]
                async for block in generate(item['question'], sources, [], metrics):
                    first = first or time.perf_counter()
                    answer += block
            except Exception as exc:
                error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '评估调用失败。'
            result = dict(item, answer=answer, sources=sources, error=error, refused=answer.strip() == REFUSAL,
                          first_token_ms=round((first - started) * 1000) if first else None,
                          duration_ms=round((time.perf_counter() - started) * 1000), metrics=metrics)
            yield {'event': 'result', 'data': json.dumps(result, ensure_ascii=False)}
        yield {'event': 'done', 'data': '{}'}

    return EventSourceResponse(stream(), ping=10)
