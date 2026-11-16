"""Password hashing, JWT access tokens and opaque token helpers."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import AppError, unauthorized
from app.core.time import utcnow

# bcrypt only uses the first 72 bytes of a password.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX_BYTES], password_hash.encode("utf-8"))
    except ValueError:
        return False


@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    workspace_id: str
    demo: bool


def access_token_ttl_seconds() -> int:
    return settings.access_token_expire_minutes * 60


def create_access_token(user_id: str, workspace_id: str, demo: bool) -> str:
    now = utcnow()
    payload = {
        "sub": user_id,
        "wid": workspace_id,
        "demo": demo,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=access_token_ttl_seconds())).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "wid", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError(401, "token_expired", "Your session has expired. Please refresh it.") from exc
    except jwt.PyJWTError as exc:
        raise unauthorized() from exc
    return AccessClaims(user_id=payload["sub"], workspace_id=payload["wid"], demo=bool(payload.get("demo")))


def new_opaque_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """Refresh and reset tokens are stored as SHA-256 hashes, never in plain text."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
