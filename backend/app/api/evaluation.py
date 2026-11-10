import asyncio
import json
import re
import time
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse
from ..errors import fail
from ..config import settings
from ..db import engine
from ..models import Document
from ..rag.ingest import index_lock
from ..rag.retrieve import hybrid_search
from ..rag.generate import generate, clinical_request
from ..rag.prompts import REFUSAL

router = APIRouter(prefix='/api/evaluation', tags=['evaluation'])
EVAL = Path(__file__).resolve().parents[3] / 'eval'
Dataset = Literal['template', 'zh', 'en']
DATASETS = [
    {'id': 'zh', 'title': '中文学习资料', 'description': '8 道原页核对题 + 3 道资料外问题；以导入 PDF 的文件指纹匹配标准。'},
    {'id': 'en', 'title': 'Open RN 英文教材', 'description': '10 道原页核对题 + 3 道资料外问题；英文提问，用于检验英文教材检索。'},
    {'id': 'template', 'title': '通用医学模板', 'description': '20 道通用题，未绑定具体教材；应先确认资料覆盖情况。'},
]


class EvaluationRequest(BaseModel):
    dataset: Dataset = 'template'
    question_ids: list[int] = Field(default_factory=list, max_length=100)
    document_ids: list[int] | None = Field(default=None, max_length=100)


def load_questions(dataset):
    if dataset == 'template':
        return json.loads((EVAL / 'questions.json').read_text(encoding='utf-8'))
    prefix = 'chinese' if dataset == 'zh' else 'open'
    content = json.loads((EVAL / f'{prefix}-book-questions.json').read_text(encoding='utf-8'))
    books = {book['id']: book for book in json.loads((EVAL / f'{prefix}-books.json').read_text(encoding='utf-8'))['books']}
    items = []
    for index, question in enumerate(content['questions'], 1):
        book = books[question['book']]
        items.append({'id': index, 'question': question['question'], 'category': book['title'],
                      'expected_refusal': False, 'expected_pages': question['expected_pages'],
                      'expected_file_hash': book['sha256'], 'book_title': book['title']})
    refusals = json.loads((EVAL / 'open-book-questions.json').read_text(encoding='utf-8'))['refusal_questions']
    for question in refusals:
        items.append({'id': len(items) + 1, 'question': question, 'category': '资料外问题', 'expected_refusal': True})
    return items


@router.get('/datasets')
def datasets():
    return {'items': [dict(dataset, count=len(load_questions(dataset['id']))) for dataset in DATASETS]}


@router.get('/questions')
def questions(dataset: Dataset = 'template'):
    with Session(engine) as session:
        hashes = {doc.file_hash for doc in session.exec(select(Document)).all() if doc.status == 'ready'}
    items = [dict(q, available=not q.get('expected_file_hash') or q['expected_file_hash'] in hashes)
             for q in load_questions(dataset)]
    return {'dataset': dataset, 'items': items}


def expected_page_hit(item, sources):
    """Compare independent file/page labels; this does not grade answer semantics."""
    if item.get('expected_refusal') or not item.get('expected_file_hash'):
        return None
    return any(source.get('file_hash') == item['expected_file_hash']
               and any(source['page_start'] <= page <= source['page_end'] for page in item['expected_pages'])
               for source in sources)


@router.post('/run')
async def run(payload: EvaluationRequest, request: Request):
    if index_lock.locked():
        fail(409, 'INDEX_BUSY', '入库完成后才能评估。')
    all_items = questions(payload.dataset)['items']
    if set(payload.question_ids) - {item['id'] for item in all_items}:
        fail(400, 'INVALID_QUESTIONS', '评估题目不存在。')
    if payload.document_ids is not None:
        with Session(engine) as session:
            valid = {doc.id for doc in session.exec(select(Document)).all()}
        if set(payload.document_ids) - valid:
            fail(400, 'INVALID_DOCUMENTS', '筛选中存在无效文档。')
    items = [q for q in all_items if not payload.question_ids or q['id'] in payload.question_ids]

    async def stream():
        for item in items:
            if await request.is_disconnected():
                return
            yield {'event': 'progress', 'data': json.dumps({'id': item['id'], 'question': item['question']}, ensure_ascii=False)}
            started, first = time.perf_counter(), None
            answer, sources, error, metrics, retrieval_hit = '', [], '', {}, None
            try:
                if not item['available']:
                    raise ValueError('标准对应的教材未就绪或文件版本不符，请先导入并建立索引。')
                retrieval_started = time.perf_counter()
                sources = [] if clinical_request(item['question']) else await asyncio.to_thread(
                    hybrid_search, item['question'], 6, payload.document_ids)
                if not settings.llm_api_key:
                    sources = sources[:3]
                metrics['retrieval_ms'] = round((time.perf_counter() - retrieval_started) * 1000)
                retrieval_hit = expected_page_hit(item, sources)
                async for block in generate(item['question'], sources, [], metrics):
                    if await request.is_disconnected():
                        return
                    first = first or time.perf_counter()
                    answer += block
                if answer.strip() == REFUSAL:
                    sources = []
                elif settings.llm_api_key:
                    used = {int(ref) for ref in re.findall(r'\[(\d+)\]', answer)}
                    sources = [source for source in sources if source['index'] in used]
            except Exception as exc:
                error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '评估调用失败。'
            result = dict(item, answer=answer, sources=sources, error=error, refused=answer.strip() == REFUSAL,
                          retrieval_hit=retrieval_hit,
                          first_token_ms=round((first - started) * 1000) if first else None,
                          duration_ms=round((time.perf_counter() - started) * 1000), metrics=metrics)
            yield {'event': 'result', 'data': json.dumps(result, ensure_ascii=False)}
        yield {'event': 'done', 'data': '{}'}

    return EventSourceResponse(stream(), ping=10)
