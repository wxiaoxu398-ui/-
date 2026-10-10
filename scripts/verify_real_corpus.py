"""Verify full, licensed PDFs in isolated storage; never mutate the user's library."""
import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
import pymupdf
from download_open_books import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def normalize(text):
    return re.sub(r'\s+', '', text).lower()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['bge', 'lexical'], default='bge')
    parser.add_argument('--books', type=Path, default=ROOT / '.local/acceptance/books')
    parser.add_argument('--live', action='store_true', help='Use the locally configured DeepSeek key and save answers for manual grading.')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'eval/open-books.json').read_text(encoding='utf-8'))
    questions = json.loads((ROOT / 'eval/open-book-questions.json').read_text(encoding='utf-8'))
    books = {book['id']: book for book in manifest['books']}
    for book in books.values():
        path = args.books / book['pdf_name']
        if not path.is_file() or fingerprint(path) != book['sha256']:
            raise ValueError(f"Missing or changed {book['pdf_name']}; run scripts/download_open_books.py first.")
        with pymupdf.open(path) as pdf:
            assert len(pdf) == book['pages']
            for item in questions['questions']:
                if item['book'] == book['id']:
                    assert normalize(item['anchor']) in normalize(pdf[item['anchor_page'] - 1].get_text(sort=True)), f"Expected passage changed: {item['id']}"
    workspace = ROOT / '.local/acceptance' / args.backend
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ.update(BOOKS_DIR=str(args.books.resolve()), DB_PATH=str(workspace / 'test.db'),
                      CHROMA_DIR=str(workspace / 'chroma'), MODEL_CACHE_DIR=str(ROOT / 'backend/app/data/models'),
                      EMBEDDING_BACKEND=args.backend)
    if not args.live:
        os.environ['LLM_API_KEY'] = ''
    sys.path.insert(0, str(ROOT / 'backend'))
    from app.config import settings
    from app.db import init_db, engine
    from app.models import Document
    from app.rag.ingest import ingest_all, index_lock, state
    from app.rag.retrieve import hybrid_search
    from app.rag.generate import generate
    from app.rag.prompts import REFUSAL
    from sqlmodel import Session, select
    if args.live and not settings.llm_api_key:
        raise ValueError('DeepSeek key is not configured. Enter it in the app settings; live acceptance was not run.')
    init_db()
    index_lock.acquire()
    started = time.perf_counter()
    worker = threading.Thread(target=ingest_all, args=(False,), daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(15)
        if worker.is_alive():
            print(f"Indexing {state['processed']}/{state['total']}: {state['current']}", flush=True)
    assert state['status'] == 'complete', state
    with Session(engine) as session:
        docs = session.exec(select(Document)).all()
        assert len(docs) == 3 and all(doc.status == 'ready' for doc in docs)
        report = {'backend': args.backend, 'generated_answers': bool(args.live), 'index_seconds': round(time.perf_counter() - started, 2),
                  'documents': [{key: getattr(doc, key) for key in ('title', 'num_pages', 'num_chunks', 'file_hash')} for doc in docs],
                  'index_seconds_note': '当前运行包含增量检查与预热；已有就绪文档会跳过，不代表全量重建耗时。',
                  'results': [], 'refusals': []}
        book_by_doc = {doc.id: next(book for book in books.values() if book['pdf_name'] == doc.file_name) for doc in docs}
    pdfs = {book['id']: pymupdf.open(args.books / book['pdf_name']) for book in books.values()}
    try:
        async def run_question(question, history=None):
            begin = time.perf_counter()
            sources = hybrid_search(question, history=history or [])
            retrieval_ms = round((time.perf_counter() - begin) * 1000)
            answer, first_ms, error, metrics = '', None, '', {}
            if args.live:
                try:
                    async for block in generate(question, sources, history or [], metrics):
                        if first_ms is None:
                            first_ms = round((time.perf_counter() - begin) * 1000)
                        answer += block
                except Exception as exc:
                    error = str(exc)
            for source in sources:
                book = book_by_doc[source['document_id']]
                page = source['page_start']
                assert source['page_end'] == page and 1 <= page <= book['pages']
                assert source['file_hash'] == book['sha256']
                assert normalize(source['content']) in normalize(pdfs[book['id']][page - 1].get_text(sort=True)), f'Incorrect PDF mapping: {source}'
            return dict(question=question, retrieval_ms=retrieval_ms, sources=sources,
                        answer=answer if args.live else None, first_token_ms=first_ms,
                        duration_ms=round((time.perf_counter() - begin) * 1000), error=error, metrics=metrics)
        for item in questions['questions']:
            result = asyncio.run(run_question(item['question']))
            is_expected = lambda source: book_by_doc[source['document_id']]['id'] == item['book'] and source['page_start'] in item['expected_pages']
            result.update(id=item['id'], expected_page_hit=any(is_expected(source) for source in result['sources']),
                          top_page_hit=bool(result['sources']) and is_expected(result['sources'][0]),
                          generated_claims_judgment=None)
            report['results'].append(result)
            print(f"Q{item['id']}: expected page {result['expected_page_hit']}; top page {result['top_page_hit']}; retrieval {result['retrieval_ms']} ms", flush=True)
        for question in questions['refusal_questions']:
            result = asyncio.run(run_question(question))
            result['passed'] = not result['sources'] and (not args.live or result['answer'].strip() == REFUSAL)
            report['refusals'].append(result)
        report['followup'] = asyncio.run(run_question('What about its four stages?', [{'role': 'user', 'content': 'Explain pharmacokinetics.'}]))
        report['followup']['passed'] = any(book_by_doc[source['document_id']]['id'] == 'pharmacology2e' and source['page_start'] == 33 for source in report['followup']['sources'])
        report['expected_page_recall'] = sum(result['expected_page_hit'] for result in report['results']) / len(report['results'])
        report['top_page_recall'] = sum(result['top_page_hit'] for result in report['results']) / len(report['results'])
        report['pdf_mapping_checked'] = True
        report['note'] = 'English questions on three complete English textbooks. Expected-page recall is not generated-answer citation accuracy; generated claims require manual review. No LLM latency or cost claim is made by a retrieval-only run.'
        output = workspace / ('live-results.json' if args.live else 'retrieval-results.json')
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: report[key] for key in ('backend', 'index_seconds', 'expected_page_recall', 'top_page_recall', 'generated_answers')}, ensure_ascii=False), flush=True)
        print('Report:', output, flush=True)
        assert report['expected_page_recall'] >= 0.8, 'Expected-page recall below 80%.'
        assert all(result['passed'] for result in report['refusals']), 'Out-of-corpus refusal failed.'
        assert report['followup']['passed'], 'Follow-up retrieval failed.'
    finally:
        for pdf in pdfs.values():
            pdf.close()


if __name__ == '__main__':
    main()
