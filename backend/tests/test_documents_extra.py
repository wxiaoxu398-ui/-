import time
import pymupdf
from app.config import settings


def test_upload_pdf_metadata_and_collision(client):
    pdf = pymupdf.open(); page = pdf.new_page(); page.insert_text((50, 70), 'A unique software fixture for document upload validation.')
    payload = pdf.tobytes(); pdf.close()
    response = client.post('/api/documents/upload', files={'file': ('网页导入测试.pdf', payload, 'application/pdf')})
    assert response.status_code == 200, response.text
    assert client.post('/api/documents/upload', files={'file': ('网页导入测试.pdf', payload, 'application/pdf')}).status_code == 409
    doc = next(d for d in client.get('/api/documents').json()['items'] if d['file_name'] == '网页导入测试.pdf')
    assert client.patch(f"/api/documents/{doc['id']}", json={'title': '网页导入测试', 'author': '软件测试', 'source': '测试夹具', 'license': '测试用途', 'source_url': ''}).status_code == 200
    assert client.get(f"/api/documents/{doc['id']}").json()['license'] == '测试用途'
    # Remove this exact newly created fixture, keeping all other test PDFs untouched.
    (settings.books_dir / '网页导入测试.pdf').unlink()
    from app.db import engine
    from app.models import Document
    from sqlmodel import Session
    with Session(engine) as session:
        session.delete(session.get(Document, doc['id'])); session.commit()


def test_pdf_disappearance_invalidates_retrieval_cache(client):
    from app.rag.retrieve import hybrid_search
    from app.rag.ingest import scan_documents
    from app.db import engine
    from app.models import Document
    from sqlmodel import Session, select
    # This test runs before the main index tests; use an isolated document.
    file = settings.books_dir / '缓存测试.pdf'
    pdf = pymupdf.open(); page = pdf.new_page(); page.insert_text((50, 70), 'cache-unique-token'); pdf.save(file); pdf.close()
    scan_documents()
    with Session(engine) as session:
        doc = session.exec(select(Document).where(Document.file_name == file.name)).first()
        doc.status = 'ready'; doc.indexed_backend = 'lexical'
        from app.models import Chunk
        document_id = doc.id
        session.add(doc); session.add(Chunk(document_id=document_id, chunk_index=0, content='缓存独有词条测试', page_start=1, page_end=1)); session.commit()
    from app.rag.retrieve import invalidate
    invalidate()
    assert hybrid_search('缓存独有词条测试')
    file.unlink()
    scan_documents()
    assert not hybrid_search('缓存独有词条测试')
    with Session(engine) as session:
        from sqlalchemy import delete
        session.exec(delete(Chunk).where(Chunk.document_id == document_id)); session.exec(delete(Document).where(Document.id == document_id)); session.commit()
    invalidate()
