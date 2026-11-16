"""Periodic housekeeping: expired demo workspaces and stale tokens."""

import asyncio
import logging

from sqlalchemy import delete, or_

from app.core.database import SessionLocal
from app.core.time import utcnow
from app.models import PasswordResetToken, RefreshToken
from app.services.demo import purge_expired_demos

logger = logging.getLogger("promptdox.maintenance")

INTERVAL_SECONDS = 3600


async def run_once() -> None:
    async with SessionLocal() as db:
        await purge_expired_demos(db)
        now = utcnow()
        await db.execute(delete(RefreshToken).where(RefreshToken.expires_at < now))
        await db.execute(
            delete(PasswordResetToken).where(
                or_(PasswordResetToken.expires_at < now, PasswordResetToken.used_at.is_not(None))
            )
        )
        await db.commit()


async def maintenance_loop() -> None:
    while True:
        try:
            await run_once()
        except Exception:  # noqa: BLE001
            logger.exception("Maintenance run failed")
        await asyncio.sleep(INTERVAL_SECONDS)
