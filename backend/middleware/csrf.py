from hmac import compare_digest

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from config.config import settings

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_EXEMPT_PATH_PREFIXES = (
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/health",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _SAFE_METHODS:
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PATH_PREFIXES):
            return await call_next(request)
        if not path.startswith("/api/"):
            return await call_next(request)

        cookie = request.cookies.get(settings.CSRF_COOKIE_NAME)
        header = request.headers.get(settings.CSRF_HEADER_NAME)
        if not cookie or not header or not compare_digest(cookie, header):
            return JSONResponse(
                {"detail": "CSRF token missing or invalid"},
                status_code=403,
            )
        return await call_next(request)
