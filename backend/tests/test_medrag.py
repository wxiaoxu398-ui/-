import json
import time
from sqlmodel import Session, select
from app.db import engine
from app.models import Message
from app.rag.generate import valid_block, clinical_request
from app.rag.prompts import REFUSAL
from app.rag.retrieve import contextual_query
from app.rag.splitter import split_pages
from conftest import CLAIMS


def parse_sse(response):
    events = []
    for block in response.text.replace('\r\n', '\n').split('\n\n'):
        lines = block.splitlines()
        event = next((line[6:].strip() for line in lines if line.startswith('event:')), None)
        data = next((line[5:].strip() for line in lines if line.startswith('data:')), None)
        if event and data:
            events.append((event, json.loads(data)))
    return events


def test_index_three_pdfs_including_500_pages(client):
    docs = client.get('/api/documents').json()['items']
    assert len(docs) == 3
    assert max(d['num_pages'] for d in docs) == 500
    response = client.post('/api/documents/ingest', json={'rebuild': True})
    assert response.status_code == 200
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        data = client.get('/api/documents').json()
        if data['task']['status'] != 'processing':
            break
        time.sleep(0.05)
    assert data['task']['status'] == 'complete', data
    assert all(d['status'] == 'ready' and d['num_chunks'] for d in data['items'])


def test_ten_in_corpus_questions_return_exact_pages(client):
    for query, original, expected_page in CLAIMS:
        events = parse_sse(client.post('/api/chat/stream', json={'question': query}))
        assert events[0][0] == 'meta' and events[1][0] == 'sources'
        assert events[-1][0] == 'done'
        sources = next(data['sources'] for event, data in events if event == 'sources')
        assert sources and sources[0]['page_start'] == expected_page, (query, sources)
        assert original in sources[0]['content']
        answer = ''.join(data['content'] for event, data in events if event == 'delta')
        assert '[1]' in answer and original in answer
        assert events[0][1]['mode'] == 'extractive'


def test_three_out_of_corpus_questions_are_refused(client):
    for question in ('明天的上证指数会涨多少？', '我的账号密码是什么？', '预测下一期彩票中奖号码。'):
        events = parse_sse(client.post('/api/chat/stream', json={'question': question}))
        assert ''.join(d['content'] for e, d in events if e == 'delta') == REFUSAL
        assert events[-1][1]['finish_reason'] == 'refused'


def test_followup_and_persisted_history_feedback_delete(client):
    first = parse_sse(client.post('/api/chat/stream', json={'question': '心肌动作电位'}))
    cid = first[0][1]['conversation_id']
    followup = parse_sse(client.post('/api/chat/stream', json={'question': '它分几期？', 'conversation_id': cid}))
    assert next(d['sources'] for e, d in followup if e == 'sources')
    mid = followup[0][1]['message_id']
    assert client.post('/api/feedback', json={'message_id': mid, 'rating': 'up'}).status_code == 200
    assert client.post('/api/feedback', json={'message_id': mid, 'rating': 'down'}).status_code == 200
    messages = client.get(f'/api/conversations/{cid}/messages').json()['items']
    assert len(messages) == 4 and messages[-1]['feedback'] == 'down'
    with Session(engine) as session:
        assert session.get(Message, mid).content == messages[-1]['content']
    assert client.delete(f'/api/conversations/{cid}').status_code == 200
    assert client.get(f'/api/conversations/{cid}/messages').status_code == 404


def test_document_filter_empty_library_and_guard(client):
    assert next(d['sources'] for e, d in parse_sse(client.post('/api/chat/stream', json={'question': '心肌动作电位', 'document_ids': []})) if e == 'sources') == []
    docs = client.get('/api/documents').json()['items']
    immune_id = next(d['id'] for d in docs if d['title'] == '免疫测试')
    events = parse_sse(client.post('/api/chat/stream', json={'question': '心肌动作电位', 'document_ids': [immune_id]}))
    assert events[-1][1]['finish_reason'] == 'refused'
    clinical = parse_sse(client.post('/api/chat/stream', json={'question': '我胸痛应该吃什么药？'}))
    assert clinical[-1][1]['finish_reason'] == 'refused'


def test_errors_and_pdf_identity(client):
    assert client.post('/api/chat/stream', json={'question': ' '}).status_code == 400
    assert client.post('/api/chat/stream', json={'question': '测试', 'top_k': 99}).status_code == 400
    assert client.post('/api/chat/stream', json={'question': '测试', 'conversation_id': 99999}).status_code == 404
    doc = client.get('/api/documents').json()['items'][0]
    assert client.get(f"/api/documents/{doc['id']}/file?hash=changed").status_code == 409
    response = client.get(f"/api/documents/{doc['id']}/file")
    assert response.status_code == 200 and response.headers['content-type'] == 'application/pdf'
    assert 'file_path' not in doc
    assert client.post('/api/documents/upload', files={'file': ('invalid.pdf', b'not a pdf', 'application/pdf')}).status_code == 400


def test_broken_and_scanned_pdf_report_failures(client):
    import pymupdf as fitz
    from app.config import settings
    empty = fitz.open(); empty.new_page(); empty.save(settings.books_dir / '扫描测试.pdf'); empty.close()
    (settings.books_dir / '损坏测试.pdf').write_bytes(b'bad')
    assert client.post('/api/documents/ingest', json={'rebuild': False}).status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = client.get('/api/documents').json()
        if data['task']['status'] != 'processing':
            break
        time.sleep(0.05)
    failures = [d for d in data['items'] if d['status'] == 'failed']
    assert len(failures) == 2 and any('OCR' in d['error'] for d in failures)


def test_citation_validation_and_page_boundaries():
    assert valid_block('第一结论[1]。第二结论[2]。', 2)
    assert not valid_block('没有依据的结论。第二结论[1]。', 1)
    assert not valid_block('伪造编号[99]。', 2)
    assert not valid_block('根据我的知识[1]。', 1)
    assert valid_block('| 分期 | 说明 |\n| --- | --- |\n| 零期 | 测试结论[1] |', 1)
    assert not valid_block('| 分期 | 说明 |\n| --- | --- |\n| 零期 | 无依据结论 |', 1)
    assert clinical_request('帮我诊断一下')
    chunks = list(split_pages(['甲' * 900, '乙' * 900]))
    assert all(c['page_start'] == c['page_end'] for c in chunks)
    assert all('乙' not in c['content'] for c in chunks if c['page_start'] == 1)
    assert '心肌' in contextual_query('它的机制呢？', [{'role': 'user', 'content': '心肌动作电位'}])


def test_evaluation_questions_no_fabricated_accuracy(client):
    questions = client.get('/api/evaluation/questions').json()['items']
    assert len(questions) == 20 and sum(q['expected_refusal'] for q in questions) == 3
    events = parse_sse(client.post('/api/evaluation/run', json={'question_ids': [18, 19, 20]}))
    results = [d for e, d in events if e == 'result']
    assert len(results) == 3 and all(r['refused'] for r in results)
    assert all('citation_accuracy' not in r for r in results)
