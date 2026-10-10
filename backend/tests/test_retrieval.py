from app.rag import retrieve
from app.config import settings


def test_explicit_definition_beats_repeated_mentions(monkeypatch):
    # Repetition in application notes should not displace the actual glossary.
    texts = ['缓存隔离在本节提到多次。' * 12 for _ in range(20)] + ['缓存隔离：测试用术语的说明。']
    doc = {'id': 99, 'title': '检索测试夹具', 'source': '软件测试', 'license': '测试用途', 'file_hash': 'test'}
    rows = [({'id': i, 'content': text, 'page_start': i + 1, 'page_end': i + 1, 'section': ''}, doc, retrieve.tokens(text))
            for i, text in enumerate(texts)]
    monkeypatch.setattr(retrieve, 'corpus', lambda: rows)
    monkeypatch.setattr(settings, 'embedding_backend', 'lexical')
    retrieve.search_index.cache_clear()
    try:
        for question in ('缓存隔离是什么意思？', '教材如何定义缓存隔离？'):
            assert retrieve.hybrid_search(question)[0]['page_start'] == 21
        assert not retrieve.hybrid_search('没有出现在资料中的火星发动机')
    finally:
        retrieve.search_index.cache_clear()
