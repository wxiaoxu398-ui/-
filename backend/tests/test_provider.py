import asyncio
import json
from types import SimpleNamespace
import pytest
from app.rag import generate as module
from app.config import settings


class ProviderStream:
    def __init__(self, chunks): self.chunks = chunks; self.closed = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.closed = True
    def __aiter__(self): return self
    async def __anext__(self):
        if not self.chunks: raise StopAsyncIteration
        content, finish = self.chunks.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish)])


def install_provider(monkeypatch, blocks):
    stream = ProviderStream([(b, None) for b in blocks] + [('', 'stop')])
    captured = {}
    class Client:
        def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=self)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def create(self, **kwargs): captured.update(kwargs); return stream
    monkeypatch.setattr(module, 'AsyncOpenAI', Client)
    monkeypatch.setattr(settings, 'llm_api_key', 'test-only-key')
    return captured, stream


def test_real_generation_adapter_streams_validated_paragraphs(monkeypatch):
    captured, stream = install_provider(monkeypatch, ['第一结论[', '1]。\n\n', '第二结论[1]。'])
    sources = [{'index': 1, 'content': '测试证据', 'title': '测试材料', 'page_start': 7}]
    async def consume(): return [block async for block in module.generate('教材问题', sources, [{'role': 'user', 'content': '上轮问题'}])]
    result = asyncio.run(consume())
    assert result == ['第一结论[1]。\n\n', '第二结论[1]。'] and stream.closed
    assert captured['stream'] and captured['model'] == settings.llm_model
    assert captured['messages'][1]['content'] == '上轮问题'
    assert json.loads(captured['messages'][-1]['content'].split('\n', 1)[1])['sources'] == sources


def test_provider_invalid_claim_never_reaches_frontend(monkeypatch):
    install_provider(monkeypatch, ['没有引用的结论。\n\n'])
    exposed = []
    async def consume():
        async for block in module.generate('教材问题', [{'index': 1}], []): exposed.append(block)
    with pytest.raises(ValueError, match='引用'):
        asyncio.run(consume())
    assert exposed == []


def test_provider_failure_is_redacted(monkeypatch):
    monkeypatch.setattr(settings, 'llm_api_key', 'test-only-key')
    class BadClient:
        def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=self)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def create(self, **kwargs): raise OSError('sensitive-provider-detail')
    monkeypatch.setattr(module, 'AsyncOpenAI', BadClient)
    async def consume(): return [block async for block in module.generate('问题', [{'index': 1}], [])]
    with pytest.raises(RuntimeError) as result: asyncio.run(consume())
    assert 'sensitive-provider-detail' not in str(result.value)


def test_disconnect_saves_partial_message(client, monkeypatch):
    from app.api import chat
    from app.db import engine
    from app.models import Message
    from sqlmodel import Session, select
    calls = {'count': 0}
    class Request:
        async def is_disconnected(self):
            calls['count'] += 1
            return calls['count'] > 1
    async def fake_generate(*args):
        yield '已生成的段落[1]。'
        yield '未发送的段落[1]。'
    monkeypatch.setattr(chat, 'generate', fake_generate)
    monkeypatch.setattr(chat, 'hybrid_search', lambda *args: [{'index': 1, 'content': '原文'}])
    async def consume():
        response = await chat.chat(chat.ChatRequest(question='停止测试'), Request())
        return [event async for event in response.body_iterator]
    events = asyncio.run(consume())
    meta = json.loads(events[0]['data'])
    with Session(engine) as session:
        message = session.get(Message, meta['message_id'])
        assert message.status == 'stopped' and message.content == '已生成的段落[1]。'
    assert json.loads(events[-1]['data'])['finish_reason'] == 'cancelled'
