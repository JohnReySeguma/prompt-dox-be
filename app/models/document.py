from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.database import Base
from app.core.ids import new_id
from app.core.time import utcnow

INDEXING_SUMMARY = "Indexing. A summary will appear when it finishes."


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("doc"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    format: Mapped[str] = mapped_column(String(8))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(16), default="indexing", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default=INDEXING_SUMMARY)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Links a template document to its source in app/seed/seed_data.json.
    seed_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(Text)
    paragraphs: Mapped[list[str]] = mapped_column(JSONB)


class Passage(Base):
    """An embedded chunk of one paragraph. `page`/`paragraph` are the 1-based citation target.

    Retrieval is always scoped to one document, so it runs as an exact cosine scan over that
    document's passages (via the document_id index). An HNSW index would filter *after* the
    approximate scan and could return fewer than TOP_K passages for a single document.
    """

    __tablename__ = "passages"
    __table_args__ = (Index("ix_passages_workspace_document", "workspace_id", "document_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    paragraph: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dimensions))
