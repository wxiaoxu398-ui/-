from datetime import datetime, timezone
from sqlmodel import SQLModel, Field


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    author: str = ''
    source: str = ''
    license: str = ''
    source_url: str = ''
    file_name: str = Field(index=True, unique=True)
    file_path: str
    file_hash: str = ''
    num_pages: int = 0
    num_chunks: int = 0
    status: str = 'pending'
    error: str = ''
    indexed_backend: str = ''
    created_at: str = Field(default_factory=now)


class Chunk(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key='document.id', index=True)
    chunk_index: int
    content: str
    page_start: int
    page_end: int
    section: str = ''


class Conversation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = '新的学习会话'
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key='conversation.id', index=True)
    role: str
    content: str = ''
    sources_json: str = '[]'
    metrics_json: str = '{}'
    status: str = 'complete'
    feedback: str | None = None
    created_at: str = Field(default_factory=now)


class Feedback(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    message_id: int = Field(foreign_key='message.id', index=True, unique=True)
    rating: str
    comment: str = ''
    created_at: str = Field(default_factory=now)
