"""Background indexing: parse -> pages -> passages -> embeddings -> summary -> ready."""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.tasks import spawn
from app.models import Document, DocumentPage, Passage
from app.services import ai
from app.services.chunking import Chunk, chunk_pages
from app.services.parsing import ParsedPage, ParseError, parse_document

logger = logging.getLogger("promptdox.indexing")

DEFAULT_SUGGESTIONS = ["Summarize this document", "What are the key sections?", "What should I verify first?"]
GENERIC_FAILURE = "We couldn't finish indexing this document. Please try uploading it again."

# Keep concurrent jobs low so the Gemini free tier isn't rate-limited.
_job_slots = asyncio.Semaphore(2)


def summary_excerpt(pages: list[ParsedPage] | list[DocumentPage], limit: int = 8000) -> str:
    parts: list[str] = []
    size = 0
    for page in pages:
        for text in [page.heading, *page.paragraphs]:
            parts.append(text)
            size += len(text)
            if size >= limit:
                return "\n".join(parts)[:limit]
    return "\n".join(parts)


async def embed_chunks(document_id: str, workspace_id: str, chunks: list[Chunk], start: int, span: int) -> None:
    """Embed chunks in batches, storing passages and advancing `progress` from `start` by up to `span`."""
    batch_size = max(1, settings.embedding_batch_size)
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        vectors = await ai.embed_texts([chunk.embedding_text for chunk in batch])
        async with SessionLocal() as db:
            document = await db.get(Document, document_id)
            if document is None:
                raise _DocumentGone
            db.add_all(
                Passage(
                    document_id=document_id,
                    workspace_id=workspace_id,
                    page=chunk.page,
                    paragraph=chunk.paragraph,
                    text=chunk.text,
                    embedding=vector,
                )
                for chunk, vector in zip(batch, vectors, strict=True)
            )
            document.progress = start + int(span * min(offset + len(batch), len(chunks)) / len(chunks))
            await db.commit()


class _DocumentGone(Exception):
    """The document was deleted while it was being indexed."""


async def _set_failed(document_id: str, message: str) -> None:
    async with SessionLocal() as db:
        document = await db.get(Document, document_id)
        if document is not None:
            document.status = "failed"
            document.error = message
            await db.commit()


async def index_document(document_id: str) -> None:
    async with _job_slots:
        try:
            await _run(document_id)
        except _DocumentGone:
            logger.info("Document %s was deleted during indexing", document_id)
        except ParseError as exc:
            logger.info("Document %s failed to parse: %s", document_id, exc)
            await _set_failed(document_id, str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("Indexing failed for document %s", document_id)
            await _set_failed(document_id, GENERIC_FAILURE)


async def _run(document_id: str) -> None:
    async with SessionLocal() as db:
        document = await db.get(Document, document_id)
        if document is None:
            raise _DocumentGone
        if document.status != "indexing":
            return
        if not document.file_path or not Path(document.file_path).exists():
            raise ParseError("The uploaded file is missing. Please upload it again.")
        file_path, file_format = Path(document.file_path), document.format
        workspace_id, name = document.workspace_id, document.name

        # Start clean so a re-queued job is idempotent.
        await db.execute(delete(Passage).where(Passage.document_id == document_id))
        await db.execute(delete(DocumentPage).where(DocumentPage.document_id == document_id))
        document.progress = 5
        await db.commit()

    # 1. Parse
    pages = await run_in_threadpool(parse_document, file_path, file_format)
    if len(pages) > settings.max_pages:
        raise ParseError(f"This document has more than {settings.max_pages} pages, which is the limit.")

    async with SessionLocal() as db:
        document = await db.get(Document, document_id)
        if document is None:
            raise _DocumentGone
        db.add_all(
            DocumentPage(document_id=document_id, number=p.number, heading=p.heading, paragraphs=p.paragraphs)
            for p in pages
        )
        document.page_count = len(pages)
        document.progress = 10
        await db.commit()

    # 2-3. Chunk and embed
    chunks = chunk_pages(pages)
    await embed_chunks(document_id, workspace_id, chunks, start=10, span=80)

    # 4. Summary and suggested questions (one call). A failure here doesn't fail the document.
    try:
        summary, suggestions = await ai.summarize_document(name, summary_excerpt(pages))
    except Exception:  # noqa: BLE001
        logger.exception("Summary generation failed for %s", document_id)
        summary, suggestions = "", []
    if not summary:
        summary = pages[0].paragraphs[0][:200]
    if len(suggestions) != 3:
        suggestions = DEFAULT_SUGGESTIONS

    # 5. Ready
    async with SessionLocal() as db:
        document = await db.get(Document, document_id)
        if document is None:
            raise _DocumentGone
        document.summary = summary
        document.suggestions = suggestions
        document.chunk_count = len(chunks)
        document.progress = 100
        document.status = "ready"
        document.error = None
        await db.commit()
    logger.info("Indexed %s: %d pages, %d passages", document_id, len(pages), len(chunks))


async def requeue_stuck_documents() -> int:
    """BackgroundTasks jobs are lost on restart; resume every document still `indexing`."""
    async with SessionLocal() as db:
        ids = list(
            await db.scalars(
                select(Document.id).where(Document.status == "indexing", Document.file_path.is_not(None))
            )
        )
    for document_id in ids:
        spawn(index_document(document_id), name=f"reindex:{document_id}")
    if ids:
        logger.info("Re-queued %d document(s) stuck in indexing", len(ids))
    return len(ids)
