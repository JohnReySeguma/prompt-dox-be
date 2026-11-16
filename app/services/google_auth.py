"""Google ID token verification for Sign in with Google."""

from dataclasses import dataclass

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.errors import AppError

_VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_transport = google_requests.Request()


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str


def _invalid() -> AppError:
    return AppError(401, "invalid_google_token", "We couldn't verify your Google sign-in. Please try again.")


def _verify_sync(token: str) -> dict:
    # Verifies the signature against Google's public keys, plus `aud` and `exp`.
    return google_id_token.verify_oauth2_token(token, _transport, settings.google_client_id)


async def verify_google_id_token(token: str) -> GoogleIdentity:
    if not settings.google_client_id:
        raise _invalid()
    try:
        claims = await run_in_threadpool(_verify_sync, token)
    except Exception as exc:  # noqa: BLE001  (google-auth raises ValueError and transport errors)
        raise _invalid() from exc

    if claims.get("iss") not in _VALID_ISSUERS or not claims.get("email_verified") or not claims.get("email"):
        raise _invalid()
    email = str(claims["email"]).lower()
    name = str(claims.get("name") or email.split("@")[0])[:80]
    return GoogleIdentity(sub=str(claims["sub"]), email=email, name=name)
