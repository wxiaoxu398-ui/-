import re
import threading
from functools import lru_cache
import jieba
from rank_bm25 import BM25Okapi
from sqlmodel import Session, select
from ..config import settings
from ..db import engine
from ..models import Chunk, Document

STOP = set('的 了 是 在 和 与 或 请 问 请问 什么 哪些 如何 为什么 怎么 一个 一种 这个 那个 它 其 呢 吗 中 主要 有 对 根据 教材 解释 简述 分别'.split())
STOP.update('a an the and or of to in on at by for from with is are was were be been being do does did what which who where when why how can could would should please explain describe define defined tell happens purpose differ indicate'.split())
cache_lock = threading.Lock()


def tokens(text):
    return [t for raw in jieba.lcut(text) if (t := raw.lower().strip()) and t not in STOP and re.search(r'[\w\u4e00-\u9fff]', t)]


@lru_cache(maxsize=1)
def corpus():
    with Session(engine) as session:
        rows = session.exec(select(Chunk, Document).join(Document, Chunk.document_id == Document.id)
                            .where(Document.status == 'ready', Document.indexed_backend == settings.embedding_backend)).all()
        return [(c.model_dump(), d.model_dump(), tokens(c.content)) for c, d in rows]


def invalidate():
    with cache_lock:
        corpus.cache_clear()
        search_index.cache_clear()


@lru_cache(maxsize=16)
def search_index(document_ids=None):
    rows = [row for row in corpus() if document_ids is None or row[1]['id'] in document_ids]
    if not rows:
        return rows, None, []
    return rows, BM25Okapi([row[2] or ['_'] for row in rows]), [set(row[2]) for row in rows]


def warmup():
    # Build the lexical cache during startup/indexing, before the first question.
    search_index()
    if settings.embedding_backend == 'bge' and corpus():
        from .embedder import embed
        embed(['教材检索'], query=True)


def contextual_query(question, history):
    # Only short/anaphoric follow-ups inherit earlier user questions.
    followup = (re.search(r'^(那|它|其|这些|这个|上述|其中|继续|再)|机制呢|有什么区别|为什么呢', question)
                or re.search(r'\b(it|its|they|their|them|those|these)\b', question, re.I) or len(tokens(question)) < 2)
    if followup:
        prior = [m['content'] for m in history if m['role'] == 'user'][-2:]
        return ' '.join(prior + [question])
    return question


def definition_targets(question):
    targets = []
    for pattern in (r'(?:什么是|何谓|定义|解释)\s*([^？?。]{2,30})',
                    r'([\u4e00-\u9fffA-Za-z0-9_-]{2,30}?)(?:是什么意思|的定义|的含义)'):
        for match in re.finditer(pattern, question):
            targets.append(match.group(1).strip())
    return targets


def hybrid_search(question, top_k=6, document_ids=None, history=None):
    query = contextual_query(question, history or [])
    rows, bm25, token_sets = search_index(tuple(sorted(set(document_ids))) if document_ids is not None else None)
    if not rows:
        return []
    query_tokens = tokens(query)
    if not query_tokens:
        return []
    bm_scores = bm25.get_scores(query_tokens)
    # BM25 IDF can approach zero for small libraries; coverage is a second lexical evidence signal.
    query_set = set(query_tokens)
    coverage = [len(query_set & row_tokens) / len(query_set) for row_tokens in token_sets]
    targets = definition_targets(query)
    definitions = {i for i, row in enumerate(rows) if any(
        re.search(r'(?:^|\n)\s*' + re.escape(term) + r'\s*[：:]', row[0]['content']) for term in targets)}
    # Definitions explicitly requested by the reader must not be drowned out by
    # repeated mentions elsewhere. Longer verbatim queries also carry strong
    # lexical evidence when every meaningful query token is present.
    exact_coverage = {i for i, value in enumerate(coverage) if value == 1 and len(query_set) >= 4}
    preferred = definitions | exact_coverage
    lexical = sorted(range(len(rows)), key=lambda i: (i in definitions, coverage[i], float(bm_scores[i])), reverse=True)[:10]
    lexical = [i for i in lexical if coverage[i] > 0]
    vector_scores = {}
    vectors = []
    if settings.embedding_backend == 'bge':
        from .embedder import collection, embed
        col = collection()
        if col.count():
            filters = {'document_id': {'$in': sorted({row[1]['id'] for row in rows})}}
            result = col.query(query_embeddings=embed([query], query=True), n_results=min(10, col.count()), where=filters, include=['distances', 'metadatas'])
            mapping = {row[0]['id']: i for i, row in enumerate(rows)}
            for meta, distance in zip(result['metadatas'][0], result['distances'][0]):
                idx = mapping.get(meta['chunk_id'])
                if idx is not None:
                    vector_scores[idx] = max(0.0, min(1.0, 1.0 - distance))
                    vectors.append(idx)
    # Refusal uses evidence scores, never the RRF score (which is not similarity).
    if not (definitions or max(vector_scores.values(), default=0) >= settings.similarity_threshold or
            (max(coverage, default=0) >= settings.lexical_coverage_threshold and
             (max(bm_scores, default=0) >= settings.bm25_threshold or len(rows) < 10))):
        return []
    fused = {}
    for ranked in (vectors, lexical):
        for rank, idx in enumerate(ranked, 1):
            fused[idx] = fused.get(idx, 0) + 1 / (60 + rank)
    result = []
    for idx in sorted(fused, key=lambda i: (i in preferred, fused[i], coverage[i], vector_scores.get(i, 0)), reverse=True):
        # In a local education tool, require a shared query term even for vector hits.
        # Weak semantic matches alone otherwise surface unrelated pages in repetitive PDFs.
        if coverage[idx] == 0 or (vector_scores.get(idx, 0) < settings.similarity_threshold and coverage[idx] < settings.lexical_coverage_threshold):
            continue
        chunk, doc, _ = rows[idx]
        result.append({'index': len(result) + 1, 'chunk_id': chunk['id'], 'document_id': doc['id'],
                       'title': doc['title'], 'page_start': chunk['page_start'], 'page_end': chunk['page_end'],
                       'content': chunk['content'], 'score': round(max(vector_scores.get(idx, 0), coverage[idx]), 3),
                       'source': doc['source'], 'license': doc['license'], 'file_hash': doc['file_hash'], 'section': chunk['section']})
        if len(result) == top_k:
            break
    return result
