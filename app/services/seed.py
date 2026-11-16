"""Demo template workspace, seeded from app/seed/seed_data.json.

seed_data.json is generated from the frontend mocks (document/documents.ts and
document/knowledge.ts) by scripts/generate_seed.py. The template is indexed once; every
`POST /v1/auth/demo` clones it, including its embeddings, so nothing is re-embedded.
"""

import asyncio
import json
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import Document, DocumentPage, Passage, Workspace
from app.services import ai
from app.services.chunking import chunk_pages
from app.services.indexing import DEFAULT_SUGGESTIONS, embed_chunks, summary_excerpt
from app.services.parsing import ParsedPage

logger = logging.getLogger("promptdox.seed")

SEED_FILE = Path(__file__).resolve().parent.parent / "seed" / "seed_data.json"
DEMO_WORKSPACE_NAME = "Northwind Analytics (demo)"
SEED_FAILURE = "The demo copy of this document couldn't be indexed. Please try again later."

_seed_lock = asyncio.Lock()
# After a failed indexing attempt, wait before retrying so demo sign-ins stay fast.
_RETRY_COOLDOWN_SECONDS = 300
_last_index_attempt = 0.0


@lru_cache
def seed_data() -> dict[str, Any]:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


async def get_template_workspace_id() -> str | None:
    async with SessionLocal() as db:
        return await db.scalar(select(Workspace.id).where(Workspace.is_template.is_(True)).limit(1))


async def ensure_template() -> str:
    """Create and index the template workspace if needed. Safe to call repeatedly."""
    global _last_index_attempt
    async with _seed_lock:
        workspace_id = await _create_template_rows()
        loop_time = asyncio.get_running_loop().time()
        if not _last_index_attempt or loop_time - _last_index_attempt >= _RETRY_COOLDOWN_SECONDS:
            _last_index_attempt = loop_time
            await _index_template(workspace_id)
        return workspace_id


async def _create_template_rows() -> str:
    data = seed_data()
    async with SessionLocal() as db:
        workspace = await db.scalar(select(Workspace).where(Workspace.is_template.is_(True)).limit(1))
        if workspace is None:
            workspace = Workspace(name=DEMO_WORKSPACE_NAME, is_template=True)
            db.add(workspace)
            await db.flush()
            logger.info("Created demo template workspace %s", workspace.id)

        existing = set(await db.scalars(select(Document.seed_key).where(Document.workspace_id == workspace.id)))
        for item in data["documents"]:
            if item["id"] in existing:
                continue
            knowledge = data["knowledge"].get(item["id"], {})
            document = Document(
                workspace_id=workspace.id,
                seed_key=item["id"],
                name=item["name"],
                format=item["format"],
                size_bytes=item["sizeBytes"],
                uploaded_at=datetime.fromisoformat(item["uploadedAt"].replace("Z", "+00:00")),
                status="indexing",
                progress=10,
                page_count=len(item["pages"]),
                summary=item["summary"],
                suggestions=knowledge.get("suggestions", []),
            )
            db.add(document)
            await db.flush()
            db.add_all(
                DocumentPage(
                    document_id=document.id,
                    number=page["number"],
                    heading=page["heading"],
                    paragraphs=page["paragraphs"],
                )
                for page in item["pages"]
            )
            logger.info("Seeded template document %s (%s)", document.name, document.id)
        await db.commit()
        return workspace.id


async def _index_template(workspace_id: str) -> None:
    async with SessionLocal() as db:
        pending = list(
            await db.scalars(
                select(Document).where(Document.workspace_id == workspace_id, Document.status != "ready")
            )
        )
    for document in pending:
        await _index_seed_document(document)


async def _index_seed_document(document: Document) -> None:
    async with SessionLocal() as db:
        db_pages = list(
            await db.scalars(
                select(DocumentPage).where(DocumentPage.document_id == document.id).order_by(DocumentPage.number)
            )
        )
        await db.execute(Passage.__table__.delete().where(Passage.document_id == document.id))
        await db.commit()
    pages = [ParsedPage(p.number, p.heading, list(p.paragraphs)) for p in db_pages]
    chunks = chunk_pages(pages)
    try:
        await embed_chunks(document.id, document.workspace_id, chunks, start=10, span=85)
        suggestions = list(document.suggestions or [])
        if len(suggestions) != 3:
            try:
                _, suggestions = await ai.summarize_document(document.name, summary_excerpt(pages))
            except Exception:  # noqa: BLE001
                logger.warning("Could not generate suggestions for seed document %s", document.name)
            if len(suggestions) != 3:
                suggestions = list(seed_data().get("defaultSuggestions") or DEFAULT_SUGGESTIONS)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to index seed document %s", document.name)
        async with SessionLocal() as db:
            row = await db.get(Document, document.id)
            if row is not None:
                row.status, row.error = "failed", SEED_FAILURE
                await db.commit()
        return

    async with SessionLocal() as db:
        row = await db.get(Document, document.id)
        if row is None:
            return
        row.chunk_count = (
            await db.scalar(select(func.count()).select_from(Passage).where(Passage.document_id == row.id)) or 0
        )
        row.suggestions = suggestions
        row.status, row.progress, row.error = "ready", 100, None
        await db.commit()
        logger.info("Indexed seed document %s (%d passages)", row.name, row.chunk_count)


async def seed_on_startup() -> None:
    try:
        await ensure_template()
    except Exception:  # noqa: BLE001
        logger.exception("Demo template seeding failed; it will be retried on the next demo sign-in")
