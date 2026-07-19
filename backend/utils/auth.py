from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

import bcrypt
import jwt

from config.config import settings

_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(plain):
    raw = plain.encode("utf-8") if isinstance(plain, str) else plain
    return raw[:_BCRYPT_MAX_BYTES]


def hash_password(plain):
    return bcrypt.hashpw(_to_bcrypt_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain, hashed):
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(
            _to_bcrypt_bytes(plain),
            hashed.encode("utf-8") if isinstance(hashed, str) else hashed,
        )
    except (ValueError, TypeError):
        return False


def issue_access_token(email, role, session_id, ttl_minutes=None):
    if ttl_minutes is None:
        ttl_minutes = settings.ACCESS_TOKEN_TTL_MINUTES
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=ttl_minutes)
    payload = {
        "sub": email,
        "email": email,
        "role": role,
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, ttl_minutes * 60


def generate_csrf_token():
    return token_urlsafe(32)
