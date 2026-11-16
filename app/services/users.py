"""User accounts, plan limits and usage counters."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.time import next_utc_midnight, today_utc
from app.models import Document, Passage, User, Workspace
from app.schemas.user import QueriesCounter, SettingsOut, UsageCounter, UsageOut, UserOut

DEMO_DISPLAY_EMAIL = "demo@promptdox.app"


@dataclass(frozen=True)
class PlanLimits:
    documents: int | None
    queries_per_day: int | None


# Matches PLAN_LIMITS in the frontend's mock/site.ts.
PLAN_LIMITS = {
    "free": PlanLimits(documents=10, queries_per_day=100),
    "pro": PlanLimits(documents=500, queries_per_day=None),
}


def initials(name: str) -> str:
    letters = [part[0] for part in name.split() if part and part[0].isalpha()]
    if not letters:
        return (name[:1] or "?").upper()
    if len(letters) == 1:
        return letters[0].upper()
    return (letters[0] + letters[-1]).upper()


def document_limit(user: User) -> int | None:
    return PLAN_LIMITS[user.plan].documents


def query_limit(user: User) -> int | None:
    if user.is_demo:
        return settings.demo_daily_queries
    return PLAN_LIMITS[user.plan].queries_per_day


def queries_used_today(user: User) -> int:
    """Questions asked today; the counter resets at midnight UTC."""
    return user.queries_today if user.queries_date == today_utc() else 0


def count_query(user: User) -> int:
    today = today_utc()
    if user.queries_date != today:
        user.queries_date = today
        user.queries_today = 0
    user.queries_today += 1
    return user.queries_today


def to_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        name=user.name,
        email=DEMO_DISPLAY_EMAIL if user.is_demo else user.email,
        initials=initials(user.name),
        role=user.role,
        workspace=user.workspace.name,
        member_since=user.created_at,
        google_connected=user.google_connected,
        queries_today=queries_used_today(user),
        plan=user.plan,  # type: ignore[arg-type]
        is_demo=user.is_demo,
    )


def to_settings_out(user: User) -> SettingsOut:
    return SettingsOut(
        strict_citations=user.strict_citations,
        stream_answers=user.stream_answers,
        weekly_digest=user.weekly_digest,
        allow_sharing=user.allow_sharing,
    )


async def count_documents(db: AsyncSession, workspace_id: str) -> int:
    return await db.scalar(select(func.count()).select_from(Document).where(Document.workspace_id == workspace_id)) or 0


async def build_usage(db: AsyncSession, user: User) -> UsageOut:
    passages = await db.scalar(
        select(func.count()).select_from(Passage).where(Passage.workspace_id == user.workspace_id)
    )
    return UsageOut(
        plan=user.plan,  # type: ignore[arg-type]
        documents=UsageCounter(used=await count_documents(db, user.workspace_id), limit=document_limit(user)),
        queries_today=QueriesCounter(
            used=queries_used_today(user), limit=query_limit(user), resets_at=next_utc_midnight()
        ),
        passages_indexed=passages or 0,
    )


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email.strip().lower()))


async def create_user_with_workspace(
    db: AsyncSession,
    *,
    name: str,
    email: str,
    password_hash: str | None = None,
    google_sub: str | None = None,
) -> User:
    first_name = name.split()[0] if name.split() else name
    workspace = Workspace(name=f"{first_name}'s workspace")
    db.add(workspace)
    await db.flush()
    user = User(
        workspace_id=workspace.id,
        name=name,
        email=email.strip().lower(),
        password_hash=password_hash,
        google_sub=google_sub,
        google_connected=google_sub is not None,
    )
    user.workspace = workspace
    db.add(user)
    await db.flush()
    return user
