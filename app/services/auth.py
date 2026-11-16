"""Sign-in flows, refresh-token rotation and password reset."""

import logging
from datetime import timedelta

from fastapi import BackgroundTasks
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, unauthorized
from app.core.security import (
    access_token_ttl_seconds,
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    verify_password,
)
from app.core.time import utcnow
from app.models import PasswordResetToken, RefreshToken, User
from app.schemas.auth import AuthSession
from app.services.email import send_password_reset_email
from app.services.google_auth import GoogleIdentity
from app.services.users import create_user_with_workspace, get_user_by_email, to_user_out

logger = logging.getLogger("promptdox.auth")


async def issue_session(db: AsyncSession, user: User, refresh_prefix: str = "rt") -> AuthSession:
    refresh_token = new_opaque_token(refresh_prefix)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    await db.commit()
    await db.refresh(user, ["workspace"])
    return AuthSession(
        user=to_user_out(user),
        access_token=create_access_token(user.id, user.workspace_id, user.is_demo),
        refresh_token=refresh_token,
        expires_in=access_token_ttl_seconds(),
    )


async def revoke_all_refresh_tokens(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def register(db: AsyncSession, name: str, email: str, password: str) -> AuthSession:
    if await get_user_by_email(db, email):
        raise AppError(409, "email_taken", "An account with this email already exists. Try signing in instead.")
    user = await create_user_with_workspace(db, name=name, email=email, password_hash=hash_password(password))
    return await issue_session(db, user)


async def login(db: AsyncSession, email: str, password: str) -> AuthSession:
    user = await get_user_by_email(db, email)
    if not user or user.is_demo or not verify_password(password, user.password_hash):
        raise AppError(401, "invalid_credentials", "That email and password don't match our records.")
    return await issue_session(db, user)


async def sign_in_with_google(db: AsyncSession, identity: GoogleIdentity) -> AuthSession:
    user = await db.scalar(select(User).where(User.google_sub == identity.sub))
    if user is None:
        user = await get_user_by_email(db, identity.email)
        if user is not None:
            user.google_sub = identity.sub
        else:
            user = await create_user_with_workspace(
                db, name=identity.name, email=identity.email, google_sub=identity.sub
            )
    user.google_connected = True
    return await issue_session(db, user)


async def refresh_session(db: AsyncSession, raw_token: str) -> AuthSession:
    token = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if token is None:
        raise unauthorized("Your session has ended. Please sign in again.")
    if token.revoked_at is not None:
        if token.replaced_by_id is not None:
            # A rotated token was reused: treat it as stolen and end every session.
            logger.warning("Refresh token reuse detected for user %s", token.user_id)
            await revoke_all_refresh_tokens(db, token.user_id)
            await db.commit()
        raise unauthorized("Your session has ended. Please sign in again.")
    if token.expires_at <= utcnow():
        raise unauthorized("Your session has ended. Please sign in again.")

    user = await db.get(User, token.user_id)
    if user is None:
        raise unauthorized("Your session has ended. Please sign in again.")

    new_raw = new_opaque_token("rt_demo" if user.is_demo else "rt")
    replacement = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(new_raw),
        expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(replacement)
    await db.flush()
    token.revoked_at = utcnow()
    token.replaced_by_id = replacement.id
    await db.commit()
    await db.refresh(user, ["workspace"])
    return AuthSession(
        user=to_user_out(user),
        access_token=create_access_token(user.id, user.workspace_id, user.is_demo),
        refresh_token=new_raw,
        expires_in=access_token_ttl_seconds(),
    )


async def logout(db: AsyncSession, raw_token: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(raw_token), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()


async def forgot_password(db: AsyncSession, email: str, background: BackgroundTasks) -> None:
    user = await get_user_by_email(db, email)
    if user is None or user.is_demo:
        return
    raw = new_opaque_token("pr")
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=utcnow() + timedelta(minutes=settings.password_reset_expire_minutes),
        )
    )
    await db.commit()
    link = f"{settings.frontend_base_url}/reset-password?token={raw}"
    if settings.is_development:
        # API_CONTRACT.md section 4.1: locally, the reset link is logged to the console.
        logger.info("Password reset link for %s: %s", user.email, link)
    background.add_task(send_password_reset_email, user.email, user.name, link)


async def reset_password(db: AsyncSession, raw_token: str, new_password: str) -> None:
    token = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw_token)).with_for_update()
    )
    if token is None or token.used_at is not None or token.expires_at <= utcnow():
        raise AppError(400, "invalid_reset_token", "This reset link is invalid or has expired. Request a new one.")
    user = await db.get(User, token.user_id)
    if user is None:
        raise AppError(400, "invalid_reset_token", "This reset link is invalid or has expired. Request a new one.")
    user.password_hash = hash_password(new_password)
    token.used_at = utcnow()
    await revoke_all_refresh_tokens(db, user.id)
    await db.commit()
