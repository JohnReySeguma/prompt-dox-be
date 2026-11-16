"""In-memory sliding-window rate limiter (API_CONTRACT.md section 4.8).

v1 runs a single API process with no Redis, so limits live in process memory.
"""

import math
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request

from app.core.errors import AppError


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, math.ceil(hits[0] + window_seconds - now))
                raise AppError(
                    429,
                    "rate_limited",
                    "You're sending requests too quickly. Please wait a moment and try again.",
                    {"retryAfter": retry_after},
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)
            if len(self._hits) > 10_000:
                self._prune(now, window_seconds)

    def _prune(self, now: float, window_seconds: int) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - window_seconds]:
            del self._hits[key]


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def limit_auth(request: Request) -> None:
    """10 attempts/minute per IP on auth endpoints."""
    limiter.hit(f"auth:{client_ip(request)}", 10, 60)


async def limit_demo(request: Request) -> None:
    """5 demo sessions/hour per IP."""
    limiter.hit(f"demo:{client_ip(request)}", 5, 3600)
