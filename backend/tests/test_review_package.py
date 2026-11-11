import importlib.util
from pathlib import Path
import sqlite3

from sqlmodel import SQLModel, Session, create_engine
from app.models import Chunk, Conversation, Document, Feedback, Message


spec = importlib.util.spec_from_file_location(
    'review_package', Path(__file__).resolve().parents[2] / 'scripts/build_review_package.py'
)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def test_runtime_source_filter_excludes_personal_and_generated_inputs():
    for name in ('backend/.env', 'backend/.env.local', '.local/server.log',
                 'submission/team-info.json', 'backend/app/data/medrag.db',
                 'backend/app/data/books/private.pdf', '../private.txt', 'C:/private.txt',
                 '.venv/pyvenv.cfg', 'deliverables/old/package.json'):
        assert not package.source_allowed(name), name
    for name in ('backend/.env.example', 'start-medrag.cmd', 'scripts/start.ps1',
                 'submission/team-info.example.json', 'eval/chinese-books.json'):
        assert package.source_allowed(name), name


def test_export_creates_only_public_textbook_rows_and_relative_page_paths(tmp_path):
    original = tmp_path / 'original.db'
    engine = create_engine(f'sqlite:///{original.as_posix()}')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Document(id=1, title='User-edited title', file_name='public.pdf',
                             file_path='C:/private-home/public.pdf', file_hash='f' * 64,
                             num_pages=1, num_chunks=1, status='ready', indexed_backend='bge'))
        session.add(Chunk(id=1, document_id=1, chunk_index=0, content='Public textbook quote.',
                          page_start=1, page_end=1))
        session.add(Conversation(id=7, title='PRIVATE-CONVERSATION'))
        session.add(Message(id=9, conversation_id=7, role='user', content='PRIVATE-USER-QUESTION'))
        session.add(Feedback(id=11, message_id=9, rating='down', comment='PRIVATE-FEEDBACK'))
        session.commit()
    engine.dispose()
    approved = {'public.pdf': {'sha256': 'f' * 64, 'title': 'Approved public title',
                               'source': 'Test public fixture', 'license': 'Test only'}}
    documents, chunks = package.read_library(original, approved)
    target = tmp_path / 'new/backend/app/data/medrag.db'
    package.export_database(target, documents, chunks, approved)
    with sqlite3.connect(target) as connection:
        assert connection.execute('SELECT title,file_path FROM document').fetchone() == (
            'Approved public title', 'app/data/books/public.pdf'
        )
        assert connection.execute('SELECT content FROM chunk').fetchone() == ('Public textbook quote.',)
        for table in ('conversation', 'message', 'feedback'):
            assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert b'PRIVATE-' not in target.read_bytes()
    assert b'private-home' not in target.read_bytes()


def test_vector_seed_rejects_unapproved_embeddings(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    with sqlite3.connect(source / 'chroma.sqlite3') as connection:
        connection.execute('CREATE TABLE embedding_metadata (key TEXT, int_value INTEGER)')
        connection.execute('CREATE TABLE embeddings (id INTEGER PRIMARY KEY)')
        connection.executemany('INSERT INTO embedding_metadata VALUES (?,?)', [('document_id', 1), ('document_id', 99)])
        connection.executemany('INSERT INTO embeddings VALUES (?)', [(1,), (2,)])
    import pytest
    with pytest.raises(package.PackageError, match='额外资料'):
        package.copy_vector_seed(source, tmp_path / 'seed', [{'id': 1, 'num_chunks': 1}], [{'id': 1}])
    assert not (tmp_path / 'seed').exists()
