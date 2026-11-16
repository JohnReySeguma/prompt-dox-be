"""Private demo workspaces, cloned from the pre-indexed template."""

import logging
from datetime import timedelta

from sqlalchemy import delete, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import new_id
from app.core.time import utcnow
from app.models import Document, DocumentPage, Message, Passage, User, Workspace
from app.schemas.auth import AuthSession
from app.services.auth import issue_session
from app.services.seed import DEMO_WORKSPACE_NAME, ensure_template, seed_data
from app.services.storage import delete_workspace_files

logger = logging.getLogger("promptdox.demo")

DEMO_USER_NAME = "Demo visitor"


async def _clone_documents(db: AsyncSession, template_id: str, workspace_id: str) -> dict[str, str]:
    """Copy documents, pages and passages (with embeddings). Returns {seed_key: new document id}."""
    templates = list(
        await db.scalars(select(Document).where(Document.workspace_id == template_id).order_by(Document.uploaded_at))
    )
    by_seed_key: dict[str, str] = {}
    for template in templates:
        clone = Document(
            id=new_id("doc"),
            workspace_id=workspace_id,
            seed_key=template.seed_key,
            name=template.name,
            format=template.format,
            size_bytes=template.size_bytes,
            uploaded_at=template.uploaded_at,
            status=template.status,
            progress=template.progress,
            chunk_count=template.chunk_count,
            page_count=template.page_count,
            summary=template.summary,
            error=template.error,
            suggestions=list(template.suggestions or []),
        )
        db.add(clone)
        await db.flush()
        await db.execute(
            insert(DocumentPage).from_select(
                ["document_id", "number", "heading", "paragraphs"],
                select(
                    literal(clone.id), DocumentPage.number, DocumentPage.heading, DocumentPage.paragraphs
                ).where(DocumentPage.document_id == template.id),
            )
        )
        await db.execute(
            insert(Passage).from_select(
                ["document_id", "workspace_id", "page", "paragraph", "text", "embedding"],
                select(
                    literal(clone.id),
                    literal(workspace_id),
                    Passage.page,
                    Passage.paragraph,
                    Passage.text,
                    Passage.embedding,
                ).where(Passage.document_id == template.id),
            )
        )
        if template.seed_key:
            by_seed_key[template.seed_key] = clone.id
    return by_seed_key


def _seed_conversations(user_id: str, documents: dict[str, str]) -> list[Message]:
    messages: list[Message] = []
    started = utcnow() - timedelta(minutes=5)
    for seed_key, chat in seed_data().get("initialChats", {}).items():
        document_id = documents.get(seed_key)
        if document_id is None:
            continue
        for offset, item in enumerate(chat):
            messages.append(
                Message(
                    document_id=document_id,
                    user_id=user_id,
                    role=item["role"],
                    content=item["content"],
                    citations=item.get("citations", []),
                    created_at=started + timedelta(seconds=5 * offset),
                )
            )
    return messages


async def create_demo_session(db: AsyncSession) -> AuthSession:
    template_id = await ensure_template()

    workspace = Workspace(name=DEMO_WORKSPACE_NAME, is_demo=True)
    db.add(workspace)
    await db.flush()
    user_id = new_id("usr_demo", 8)
    user = User(
        id=user_id,
        workspace_id=workspace.id,
        name=DEMO_USER_NAME,
        # Unique internal address; the API always displays demo@promptdox.app.
        email=f"{user_id}@demo.promptdox.invalid",
        is_demo=True,
    )
    user.workspace = workspace
    db.add(user)
    await db.flush()

    documents = await _clone_documents(db, template_id, workspace.id)
    db.add_all(_seed_conversations(user.id, documents))
    logger.info("Created demo workspace %s with %d documents", workspace.id, len(documents))
    return await issue_session(db, user, refresh_prefix="rt_demo")


async def purge_expired_demos(db: AsyncSession) -> int:
    """Delete demo workspaces older than DEMO_TTL_HOURS (cascades to users, documents, messages)."""
    cutoff = utcnow() - timedelta(hours=settings.demo_ttl_hours)
    ids = list(
        await db.scalars(select(Workspace.id).where(Workspace.is_demo.is_(True), Workspace.created_at < cutoff))
    )
    if not ids:
        return 0
    await db.execute(delete(Workspace).where(Workspace.id.in_(ids)))
    await db.commit()
    for workspace_id in ids:
        delete_workspace_files(workspace_id)
    logger.info("Purged %d expired demo workspace(s)", len(ids))
    return len(ids)
