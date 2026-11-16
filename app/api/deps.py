"""Shared request dependencies: authentication, tenancy and rate limits."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import document_not_found, unauthorized
from app.core.ratelimit import limiter
from app.core.security import AccessClaims, decode_access_token
from app.models import Document, User

DbSession = Annotated[AsyncSession, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False, description="Access token from an AuthSession")


async def get_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AccessClaims:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise unauthorized()
    claims = decode_access_token(credentials.credentials)
    # 60 requests/minute per user.
    limiter.hit(f"user:{claims.user_id}", 60, 60)
    return claims


async def get_current_user(db: DbSession, claims: Annotated[AccessClaims, Depends(get_claims)]) -> User:
    user = await db.get(User, claims.user_id)
    # The workspace always comes from the token; a mismatch means the token is stale.
    if user is None or user.workspace_id != claims.workspace_id:
        raise unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_workspace_document(db: AsyncSession, user: User, document_id: str) -> Document:
    """Load a document in the caller's workspace. Other workspaces' documents are a 404, not a 403."""
    document = await db.scalar(
        select(Document).where(Document.id == document_id, Document.workspace_id == user.workspace_id)
    )
    if document is None:
        raise document_not_found()
    return document
