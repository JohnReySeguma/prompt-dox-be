from pathlib import PurePath
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, File, Query, Response, UploadFile, status
from sqlalchemy import delete, or_, select

from app.api.deps import CurrentUser, DbSession, get_workspace_document
from app.core.config import settings
from app.core.errors import AppError, demo_restricted, document_not_ready
from app.core.ids import new_id
from app.models import Document, DocumentPage
from app.models.document import INDEXING_SUMMARY
from app.schemas.document import (
    DocumentList,
    DocumentOut,
    DocumentPatch,
    PageOut,
    PagesOut,
    SuggestionsOut,
)
from app.services.indexing import index_document
from app.services.storage import FileTooLargeError, delete_file, document_path, save_upload
from app.services.users import count_documents, document_limit

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".md": "md", ".txt": "txt"}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=DocumentList)
async def list_documents(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[Literal["ready", "processing"] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> DocumentList:
    query = select(Document).where(Document.workspace_id == user.workspace_id)
    if status_filter == "ready":
        query = query.where(Document.status == "ready")
    elif status_filter == "processing":
        query = query.where(Document.status.in_(("indexing", "failed")))
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        query = query.where(or_(Document.name.ilike(pattern), Document.summary.ilike(pattern)))
    documents = await db.scalars(query.order_by(Document.uploaded_at.desc()))
    return DocumentList(documents=[DocumentOut.model_validate(d) for d in documents])


@router.post("/upload", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    user: CurrentUser,
    db: DbSession,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(description="A .pdf, .docx, .md or .txt file, at most 50 MB")],
) -> DocumentOut:
    # Order of checks (API_CONTRACT.md 4.3): plan limit, then extension, then size.
    limit = document_limit(user)
    if limit is not None and await count_documents(db, user.workspace_id) >= limit:
        raise AppError(
            403,
            "plan_limit_reached",
            f"You've reached the {limit}-document limit on your plan.",
            {"limit": limit, "plan": user.plan},
        )
    if user.is_demo and user.workspace.demo_uploads >= settings.demo_max_uploads:
        raise demo_restricted(
            f"The demo allows {settings.demo_max_uploads} uploads. Create a free account to upload more."
        )

    filename = PurePath(file.filename or "").name.strip()
    extension = PurePath(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise AppError(400, "unsupported_format", "PromptDox supports .pdf, .docx, .md and .txt files.")

    document_id = new_id("doc")
    destination = document_path(user.workspace_id, document_id, extension)
    max_bytes = settings.demo_max_upload_bytes if user.is_demo else settings.max_upload_bytes
    try:
        size = await save_upload(file, destination, max_bytes)
    except FileTooLargeError:
        if user.is_demo:
            raise demo_restricted(
                f"Demo uploads can be at most {settings.demo_max_upload_bytes // (1024 * 1024)} MB."
            ) from None
        raise AppError(413, "file_too_large", "The file is larger than 50 MB.") from None
    finally:
        await file.close()
    if size == 0:
        delete_file(str(destination))
        raise AppError(400, "validation_error", "The file is empty.", {"fields": {"file": "The file is empty."}})

    document = Document(
        id=document_id,
        workspace_id=user.workspace_id,
        name=filename[:200],
        format=ALLOWED_EXTENSIONS[extension],
        size_bytes=size,
        status="indexing",
        progress=0,
        summary=INDEXING_SUMMARY,
        suggestions=[],
        file_path=str(destination),
    )
    db.add(document)
    if user.is_demo:
        user.workspace.demo_uploads += 1
    await db.commit()
    background.add_task(index_document, document.id)
    return DocumentOut.model_validate(document)


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: str, user: CurrentUser, db: DbSession) -> DocumentOut:
    return DocumentOut.model_validate(await get_workspace_document(db, user, document_id))


@router.get("/{document_id}/pages", response_model=PagesOut)
async def get_pages(
    document_id: str,
    user: CurrentUser,
    db: DbSession,
    from_page: Annotated[int | None, Query(alias="from", ge=1)] = None,
    to_page: Annotated[int | None, Query(alias="to", ge=1)] = None,
) -> PagesOut:
    document = await get_workspace_document(db, user, document_id)
    query = select(DocumentPage).where(DocumentPage.document_id == document.id)
    if from_page is not None:
        query = query.where(DocumentPage.number >= from_page)
    if to_page is not None:
        query = query.where(DocumentPage.number <= to_page)
    pages = list(await db.scalars(query.order_by(DocumentPage.number)))
    if not pages and document.page_count == 0:
        raise document_not_ready()
    return PagesOut(
        document_id=document.id,
        page_count=document.page_count,
        pages=[PageOut(number=p.number, heading=p.heading, paragraphs=p.paragraphs) for p in pages],
    )


@router.get("/{document_id}/suggestions", response_model=SuggestionsOut)
async def get_suggestions(document_id: str, user: CurrentUser, db: DbSession) -> SuggestionsOut:
    document = await get_workspace_document(db, user, document_id)
    if document.status != "ready":
        return SuggestionsOut(suggestions=[])
    return SuggestionsOut(suggestions=list(document.suggestions or [])[:3])


@router.patch("/{document_id}", response_model=DocumentOut)
async def rename_document(document_id: str, body: DocumentPatch, user: CurrentUser, db: DbSession) -> DocumentOut:
    document = await get_workspace_document(db, user, document_id)
    document.name = body.name
    await db.commit()
    return DocumentOut.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, user: CurrentUser, db: DbSession) -> Response:
    document = await get_workspace_document(db, user, document_id)
    file_path = document.file_path
    # Pages, passages (embeddings) and messages cascade from the document row.
    await db.execute(delete(Document).where(Document.id == document.id))
    await db.commit()
    delete_file(file_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
