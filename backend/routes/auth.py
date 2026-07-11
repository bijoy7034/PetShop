from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from config.config import settings
from enums.audit import AuditAction, ResourceType
from helpers.datetime import now_utc
from middleware.auth import _current_user
from repository.session_repo import SessionRepository
from repository.user_repo import UserRepository
from schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    TokenResponse,
)
from services.audit_service import record
from utils.auth import (
    generate_csrf_token,
    hash_password,
    issue_access_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


_ROTATION_GRACE = timedelta(seconds=10)


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        from datetime import timezone as _tz
        return dt.replace(tzinfo=_tz.utc)
    return dt


def _client_ip(request):
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _user_agent(request):
    return request.headers.get("user-agent", "")


def _cookie_kwargs(*, http_only, path="/"):
    return {
        "httponly": http_only,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "domain": settings.COOKIE_DOMAIN,
        "path": path,
    }


def _set_session_cookies(response, access_token, access_ttl, refresh_token, csrf):
    response.set_cookie(
        settings.ACCESS_COOKIE_NAME,
        access_token,
        max_age=access_ttl,
        **_cookie_kwargs(http_only=True, path="/"),
    )
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_TTL_DAYS * 86400,
        **_cookie_kwargs(http_only=True, path="/api/auth"),
    )
    response.set_cookie(
        settings.CSRF_COOKIE_NAME,
        csrf,
        max_age=settings.REFRESH_TOKEN_TTL_DAYS * 86400,
        **_cookie_kwargs(http_only=False, path="/"),
    )


def _clear_session_cookies(response):
    response.delete_cookie(
        settings.ACCESS_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path="/api/auth",
        domain=settings.COOKIE_DOMAIN,
    )
    response.delete_cookie(
        settings.CSRF_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def _issue_session(user, request, response):
    refresh_raw, session = SessionRepository.issue(
        user["_id"], user_agent=_user_agent(request), ip=_client_ip(request)
    )
    access_token, ttl = issue_access_token(user["email"], user["role"], session["_id"])
    csrf = generate_csrf_token()
    _set_session_cookies(response, access_token, ttl, refresh_raw, csrf)
    return ttl


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, response: Response):
    stored = UserRepository.by_email_with_secret(payload.email)
    if not stored:
        record(
            AuditAction.LOGIN_FAILED,
            ResourceType.AUTH,
            resource_id=payload.email,
            after={"reason": "unknown_email"},
            request=request,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if stored.get("status") != "active":
        record(
            AuditAction.LOGIN_FAILED,
            ResourceType.AUTH,
            resource_id=stored["_id"],
            actor=stored,
            after={"reason": "inactive"},
            request=request,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is inactive")

    if not verify_password(payload.password, stored.get("password_hash", "")):
        record(
            AuditAction.LOGIN_FAILED,
            ResourceType.AUTH,
            resource_id=stored["_id"],
            actor=stored,
            after={"reason": "bad_password"},
            request=request,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    ttl = _issue_session(stored, request, response)
    UserRepository.set_last_seen(stored["_id"])

    record(
        AuditAction.LOGIN_SUCCESS,
        ResourceType.AUTH,
        resource_id=stored["_id"],
        actor=stored,
        request=request,
    )

    public = UserRepository.by_id(stored["_id"])
    return TokenResponse(expires_in_seconds=ttl, user=public)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, response: Response):
    raw = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing refresh token")

    doc = SessionRepository.by_token(raw)
    if not doc:
        record(
            AuditAction.TOKEN_REFRESH_FAILED,
            ResourceType.AUTH,
            after={"reason": "unknown_token"},
            request=request,
        )
        _clear_session_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    if doc.get("revoked_at"):
        revoked_at = _aware(doc.get("revoked_at"))
        recently_rotated = (
            doc.get("revoke_reason") == "rotated"
            and revoked_at is not None
            and now_utc() - revoked_at <= _ROTATION_GRACE
        )
        if recently_rotated:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Refresh already rotated"
            )

        SessionRepository.revoke_all_for_user(doc["user_id"], "replay_detected")

        record(
            AuditAction.TOKEN_REFRESH_REUSE,
            ResourceType.AUTH,
            resource_id=str(doc["user_id"]),
            after={"session_id": str(doc["_id"])},
            request=request,
        )
        _clear_session_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token reused")

    if not SessionRepository.is_active(doc):
        _clear_session_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    user = UserRepository.by_id(str(doc["user_id"]))
    if not user or user.get("status") != "active":
        _clear_session_cookies(response)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is not active")

    new_raw, new_session = SessionRepository.rotate(
        doc, user_agent=_user_agent(request), ip=_client_ip(request)
    )
    access_token, ttl = issue_access_token(
        user["email"], user["role"], new_session["_id"]
    )
    csrf = generate_csrf_token()
    _set_session_cookies(response, access_token, ttl, new_raw, csrf)

    record(
        AuditAction.TOKEN_REFRESH,
        ResourceType.AUTH,
        resource_id=user["_id"],
        actor=user,
        after={"session_id": str(new_session["_id"])},
        request=request,
    )
    return TokenResponse(expires_in_seconds=ttl, user=user)


@router.get("/me", response_model=MeResponse)
async def me(current=Depends(_current_user)):
    return MeResponse(user=current["user"])


@router.post("/change-password", response_model=MeResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    current=Depends(_current_user),
):
    stored = UserRepository.by_email_with_secret(current["user"]["email"])
    if not stored:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if not verify_password(payload.current_password, stored.get("password_hash", "")):
        record(
            AuditAction.USER_PASSWORD_RESET,
            ResourceType.USER,
            resource_id=stored["_id"],
            actor=stored,
            after={"reason": "bad_current_password"},
            request=request,
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Current password is incorrect"
        )

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "New password must be different from the current one.",
        )

    UserRepository.update(
        stored["_id"],
        {
            "password_hash": hash_password(payload.new_password),
            "must_change_password": False,
        },
    )

    current_sid = getattr(request.state, "session_id", None)
    SessionRepository.revoke_all_for_user(
        stored["_id"],
        reason="password_changed",
        except_ids=[current_sid] if current_sid else (),
    )
    if current_sid:
        access_token, ttl = issue_access_token(
            stored["email"], stored["role"], current_sid
        )
        response.set_cookie(
            settings.ACCESS_COOKIE_NAME,
            access_token,
            max_age=ttl,
            **_cookie_kwargs(http_only=True, path="/"),
        )

    record(
        AuditAction.USER_PASSWORD_RESET,
        ResourceType.USER,
        resource_id=stored["_id"],
        actor=stored,
        after={"changed_by": "self"},
        request=request,
    )

    fresh = UserRepository.by_id(stored["_id"])
    return MeResponse(user=fresh)


@router.post("/logout")
async def logout(request: Request, response: Response, current=Depends(_current_user)):
    sid = getattr(request.state, "session_id", None)
    if sid:
        SessionRepository.revoke(sid, reason="logout")
    _clear_session_cookies(response)
    record(
        AuditAction.LOGOUT,
        ResourceType.AUTH,
        resource_id=current["user"]["_id"],
        actor=current["user"],
        request=request,
    )
    return {"status": "ok"}


@router.post("/logout-all")
async def logout_all(
    request: Request, response: Response, current=Depends(_current_user)
):
    SessionRepository.revoke_all_for_user(
        current["user"]["_id"], reason="logout_all"
    )
    _clear_session_cookies(response)
    record(
        AuditAction.LOGOUT_ALL,
        ResourceType.AUTH,
        resource_id=current["user"]["_id"],
        actor=current["user"],
        request=request,
    )
    return {"status": "ok"}
