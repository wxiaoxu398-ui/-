from typing import Literal
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[int] | None = None
    top_k: int = Field(default=6, ge=1, le=10)
    learning_mode: Literal['answer', 'outline', 'quiz'] = 'answer'


class IngestRequest(BaseModel):
    rebuild: bool = False


class MetadataRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(default='', max_length=200)
    source: str = Field(default='', max_length=200)
    license: str = Field(default='', max_length=200)
    source_url: str = Field(default='', max_length=1000)


class FeedbackRequest(BaseModel):
    message_id: int
    rating: Literal['up', 'down']
    comment: str = Field(default='', max_length=1000)
