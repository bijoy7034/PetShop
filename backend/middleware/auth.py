import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.config import settings
from enums.user import ALL_ROLES
from helpers.mongo import oid_or_none
from repository.session_repo import SessionRepository
from repository.user_repo import UserRepository

bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    description="Legacy bearer flow — cookie auth is preferred for browsers.",
    auto_error=False,
)

def _decode(token):
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


def _read_access_token(request, creds):
    token = request.cookies.get(settings.ACCESS_COOKIE_NAME)
    if token:
        return token
    if creds and creds.credentials:
        return creds.credentials
    return None


async def _current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    token = _read_access_token(request, creds)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing access token")

    claims = _decode(token)
    email = claims.get("email") or claims.get("sub")
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has no subject")

    sid = claims.get("sid")
    if sid:
        session = SessionRepository._coll().find_one({"_id": oid_or_none(sid)})
        if not SessionRepository.is_active(session):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")
        request.state.session_id = session["_id"]

    user = UserRepository.by_email(email)
    if not user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User not provisioned")
    if user.get("status") != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is not active")
    return {"user": user, "claims": claims}


def require_roles(*allowed):
    bad = set(allowed) - ALL_ROLES
    if bad:
        raise ValueError(f"Unknown role(s): {bad}")

    async def _dep(current=Depends(_current_user)):
        if current["user"]["role"] not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return current

    return _dep


require_any_user = require_roles("admin", "office_staff", "sales_rep")
require_office = require_roles("admin", "office_staff")
require_admin = require_roles("admin")
require_sales_rep = require_roles("sales_rep")
