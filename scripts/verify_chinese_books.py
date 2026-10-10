"""Check Chinese retrieval through the running local API, without LLM calls."""
import hashlib
import json
import re
import sys
from pathlib import Path
import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]


def normalize(text):
    return re.sub(r'\s+', '', text)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    books = {b['id']: b for b in json.loads((ROOT / 'eval/chinese-books.json').read_text(encoding='utf-8'))['books']}
    questions = json.loads((ROOT / 'eval/chinese-book-questions.json').read_text(encoding='utf-8'))['questions']
    pdfs = {key: pymupdf.open(ROOT / 'backend/app/data/books' / book['pdf_name']) for key, book in books.items()}
    created = []
    try:
        for key, book in books.items():
            path = ROOT / 'backend/app/data/books' / book['pdf_name']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == book['sha256']
            for item in questions:
                if item['book'] == key:
                    assert normalize(item['anchor']) in normalize(pdfs[key][item['anchor_page'] - 1].get_text(sort=True))
        with httpx.Client(base_url='http://127.0.0.1:8000/api', timeout=60) as client:
            health = client.get('/health').json()
            if health['llm_configured']:
                raise ValueError('This check requires excerpt mode so it never makes a paid LLM call.')
            assert health['task']['status'] != 'processing'
            docs = client.get('/documents').json()['items']
            by_file = {doc['file_name']: doc for doc in docs}
            by_doc = {}
            for key, book in books.items():
                doc = by_file[book['pdf_name']]
                assert doc['status'] == 'ready' and doc['num_pages'] == book['pages']
                assert doc['license'] == book['license']
                by_doc[doc['id']] = key
            results = []
            try:
                for item in questions:
                    for scope in ('book', 'all'):
                        payload = {'question': item['question']}
                        if scope == 'book':
                            payload['document_ids'] = [by_file[books[item['book']]['pdf_name']]['id']]
                        response = client.post('/chat/stream', json=payload)
                        response.raise_for_status()
                        sources, answer, done, mode = [], '', {}, None
                        for block in re.split(r'\r?\n\r?\n', response.text):
                            fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line)
                            if 'data' not in fields:
                                continue
                            data = json.loads(fields['data'])
                            if fields.get('event') == 'meta':
                                created.append(data['conversation_id'])
                                mode = data['mode']
                            elif fields.get('event') == 'sources':
                                sources = data['sources']
                            elif fields.get('event') == 'delta':
                                answer += data['content']
                            elif fields.get('event') == 'done':
                                done = data
                            elif fields.get('event') == 'error':
                                raise ValueError(data)
                        assert mode == 'extractive' and done
                        for source in sources:
                            key = by_doc[source['document_id']]
                            page = source['page_start']
                            assert source['page_end'] == page and source['file_hash'] == books[key]['sha256']
                            assert normalize(source['content']) in normalize(pdfs[key][page - 1].get_text(sort=True))
                        hit = any(by_doc[s['document_id']] == item['book'] and s['page_start'] in item['expected_pages'] for s in sources)
                        results.append({'question': item['question'], 'scope': scope, 'expected_page_hit': hit,
                            'sources': [{'book': by_doc[s['document_id']], 'page': s['page_start']} for s in sources],
                            'duration_ms': done['duration_ms'], 'finish_reason': done['finish_reason']})
                        print(f"{scope}: {item['question']} expected page {hit}; {done['duration_ms']} ms", flush=True)
            finally:
                # Delete only temporary conversations created by this exact verification run.
                for conversation_id in created:
                    client.delete(f'/conversations/{conversation_id}').raise_for_status()
            report = {'checked_at': '2026-10-11', 'generated_answers': False, 'backend': health['embedding_backend'],
                'new_pages': sum(b['pages'] for b in books.values()),
                'new_chunks': sum(by_file[b['pdf_name']]['num_chunks'] for b in books.values()),
                'pdf_mapping_checked': True, 'sources_checked': sum(len(r['sources']) for r in results),
                'results': results,
                'note': 'Only local Chinese retrieval and original-page mapping were checked. No DeepSeek generation, clinical advice, cost or generation-latency acceptance.'}
            output = ROOT / 'eval/chinese-retrieval-benchmark.json'
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            assert all(r['expected_page_hit'] for r in results if r['scope'] == 'book'), 'Some selected-book expected pages were not retrieved.'
            print('Report:', output)
    finally:
        for pdf in pdfs.values():
            pdf.close()


if __name__ == '__main__':
    main()
