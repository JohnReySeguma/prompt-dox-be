"""UTC time helpers."""

from datetime import UTC, date, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def today_utc() -> date:
    return utcnow().date()


def next_utc_midnight() -> datetime:
    now = utcnow()
    return datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)


def iso_z(value: datetime) -> str:
    """Format as ISO 8601 UTC with a Z suffix, e.g. 2026-10-28T09:14:00Z."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
