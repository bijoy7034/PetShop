"""Simple in-memory token-bucket rate limiter.

Applied as ASGI middleware, keyed by (route-group, identity) where
identity is the authenticated user_id if a session cookie is present,
else the client IP. Configurable per-route via `RATE_LIMITS` in the
settings — the default keeps auth endpoints tight (10 req/min) while
everything else runs on a lenient 300 req/min bucket.

Not distributed — a single-process design that suffices for a single-
instance deployment. If you scale horizontally, drop this and use a
Redis-backed limiter (SlowAPI + redis, e.g.).
"""
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.responses import JSONResponse

from config.config import settings


# In-memory bucket: {key: deque[timestamp]}.
_hits = defaultdict(deque)


def _identity(request):
    # Prefer authenticated cookie value; fall back to client IP.
    tok = request.cookies.get(settings.ACCESS_COOKIE_NAME)
    if tok:
        return f"cookie:{tok[-24:]}"  # last chunk is a safe pseudo-id
    xff = request.headers.get("x-forwarded-for") or ""
    ip = xff.split(",")[0].strip() if xff else (
        request.client.host if request.client else "unknown"
    )
    return f"ip:{ip}"


def _bucket_for(path):
    """Return (limit, window_seconds) for this path. Route-specific
    limits win; else the default."""
    for prefix, (limit, window) in _RULES:
        if path.startswith(prefix):
            return limit, window
    return _DEFAULT


# Ordered — most-specific first.
_RULES = [
    ("/api/auth/login",            (10, 60)),
    ("/api/auth/refresh",          (30, 60)),
    ("/api/auth/change-password",  (5,  60)),
    ("/api/users/",                (60, 60)),   # covers /users/{id}/reset-password
    ("/api/notifications/send",    (30, 60)),
    ("/api/notifications/announce",(30, 60)),
]
_DEFAULT = (300, 60)


class RateLimitMiddleware:
    """ASGI middleware. Rejects with 429 when a bucket is full."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        # Skip rate limiting entirely when the operator disables it —
        # useful for tests + local development.
        if not getattr(settings, "RATE_LIMIT_ENABLED", True):
            await self.app(scope, receive, send)
            return

        path = request.url.path
        limit, window = _bucket_for(path)
        key = f"{path}:{_identity(request)}"
        now = time.monotonic()

        q = _hits[key]
        # Drop old timestamps outside the window.
        while q and now - q[0] > window:
            q.popleft()

        if len(q) >= limit:
            retry_after = max(1, int(window - (now - q[0])))
            resp = JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit hit: {limit} requests per {window}s. "
                        f"Retry in {retry_after}s."
                    ),
                },
                headers={"Retry-After": str(retry_after)},
            )
            await resp(scope, receive, send)
            return

        q.append(now)
        await self.app(scope, receive, send)
