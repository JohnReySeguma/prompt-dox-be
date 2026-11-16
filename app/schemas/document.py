from typing import Literal

from pydantic import field_validator

from app.schemas.common import CamelModel, Citation, UtcDatetime

DocFormat = Literal["pdf", "docx", "md", "txt"]
DocStatus = Literal["indexing", "ready", "failed"]


class DocumentOut(CamelModel):
    id: str
    name: str
    format: DocFormat
    size_bytes: int
    uploaded_at: UtcDatetime
    status: DocStatus
    progress: int
    chunk_count: int
    page_count: int
    summary: str
    error: str | None


class DocumentList(CamelModel):
    documents: list[DocumentOut]


class DocumentPatch(CamelModel):
    name: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name can't be empty.")
        if len(value) > 200:
            raise ValueError("Name must be 200 characters or fewer.")
        return value


class PageOut(CamelModel):
    number: int
    heading: str
    paragraphs: list[str]


class PagesOut(CamelModel):
    document_id: str
    page_count: int
    pages: list[PageOut]


class SuggestionsOut(CamelModel):
    suggestions: list[str]


class MessageOut(CamelModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation]
    created_at: UtcDatetime


class MessageList(CamelModel):
    messages: list[MessageOut]


class ChatRequest(CamelModel):
    question: str

    @field_validator("question")
    @classmethod
    def _valid_question(cls, value: str) -> str:
        from app.core.config import settings

        value = value.strip()
        if not value:
            raise ValueError("Please enter a question.")
        if len(value) > settings.max_question_chars:
            raise ValueError(f"Questions can be at most {settings.max_question_chars:,} characters.")
        return value


class FeedbackRequest(CamelModel):
    helpful: bool
