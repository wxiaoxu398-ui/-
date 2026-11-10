import asyncio
import json
from app.api.evaluation import expected_page_hit
from app.rag import generate as generator
from app.rag.prompts import LEARNING_INSTRUCTIONS
from test_medrag import parse_sse
from test_provider import install_provider


def test_learning_mode_keeps_retrieval_question_and_persists_trace(client, monkeypatch):
    from app.api import chat
    captured = {}
    def retrieve(question, *args):
        captured['question'] = question
        return [{'index': 1, 'title': '测试材料', 'page_start': 7, 'page_end': 7, 'content': '原文。'}]
    monkeypatch.setattr(chat, 'hybrid_search', retrieve)
    events = parse_sse(client.post('/api/chat/stream', json={'question': '复习主题', 'learning_mode': 'outline'}))
    assert captured['question'] == '复习主题'
    phase = next(data for event, data in events if event == 'phase')
    assert phase['source_count'] == 1 and phase['retrieval_ms'] >= 0
    saved = client.get(f"/api/conversations/{events[0][1]['conversation_id']}/messages").json()['items'][-1]
    assert saved['metrics']['learning_mode'] == 'outline'
    assert saved['metrics']['retrieved_sources'] == 1
    assert client.post('/api/chat/stream', json={'question': '测试', 'learning_mode': 'unsafe'}).status_code == 400


def test_quiz_instructions_cannot_replace_grounding_rules(monkeypatch):
    captured, _ = install_provider(monkeypatch, ['**自测1**：测试问题[1]？\n\n**参考答案**：测试答案[1]。'])
    metrics = {}
    async def consume():
        return [text async for text in generator.generate('主题', [{'index': 1}], [], metrics, 'quiz')]
    result = asyncio.run(consume())
    assert len(result) == 2 and metrics['validated_blocks'] == 2
    system = captured['messages'][0]['content']
    assert '不得补入先验知识' in system and LEARNING_INSTRUCTIONS['quiz'] in system


def test_evaluation_sets_bind_independent_pdf_versions(client):
    sets = client.get('/api/evaluation/datasets').json()['items']
    assert {item['id']: item['count'] for item in sets} == {'zh': 11, 'en': 13, 'template': 20}
    questions = client.get('/api/evaluation/questions?dataset=zh').json()['items']
    assert len(questions[0]['expected_file_hash']) == 64 and questions[0]['expected_pages'] == [12]
    assert not questions[0]['available']  # Isolated test PDFs cannot impersonate the WHO originals.
    assert sum(question['expected_refusal'] for question in questions) == 3
    events = parse_sse(client.post('/api/evaluation/run', json={'dataset': 'zh', 'question_ids': [1]}))
    result = next(data for event, data in events if event == 'result')
    assert result['error'] and not result['answer'] and result['retrieval_hit'] is None
    assert client.post('/api/evaluation/run', json={'dataset': 'zh', 'question_ids': [999]}).status_code == 400


def test_page_hit_requires_matching_file_and_physical_page():
    item = {'expected_file_hash': 'original', 'expected_pages': [12]}
    assert expected_page_hit(item, [{'file_hash': 'original', 'page_start': 11, 'page_end': 12}])
    assert not expected_page_hit(item, [{'file_hash': 'replacement', 'page_start': 12, 'page_end': 12}])
    assert not expected_page_hit(item, [{'file_hash': 'original', 'page_start': 13, 'page_end': 13}])
    assert expected_page_hit({'expected_refusal': True}, []) is None
