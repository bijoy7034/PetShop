from datetime import timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe

from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none


def _hash(token):
    return sha256(token.encode("utf-8")).hexdigest()


def _as_oid(user_id):
    return oid_or_none(user_id) or user_id


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class SessionRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.SESSIONS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = SessionRepository._coll()
        coll.create_index("expires_at", expireAfterSeconds=0)
        coll.create_index([("refresh_hash", ASCENDING)], unique=True)
        coll.create_index([("user_id", ASCENDING)])

    @staticmethod
    def issue(user_id, user_agent=None, ip=None):
        raw = token_urlsafe(48)
        now = now_utc()
        exp = now + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
        doc = {
            "user_id": _as_oid(user_id),
            "refresh_hash": _hash(raw),
            "issued_at": now,
            "last_used_at": now,
            "expires_at": exp,
            "user_agent": (user_agent or "")[:256],
            "ip": ip or "",
            "revoked_at": None,
            "revoke_reason": None,
            "rotated_to": None,
        }
        res = SessionRepository._coll().insert_one(doc)
        doc["_id"] = res.inserted_id
        return raw, doc

    @staticmethod
    def by_token(raw_token):
        return SessionRepository._coll().find_one({"refresh_hash": _hash(raw_token)})

    @staticmethod
    def is_active(doc):
        if not doc:
            return False
        if doc.get("revoked_at"):
            return False
        exp = _aware(doc.get("expires_at"))
        if exp and exp <= now_utc():
            return False
        return True

    @staticmethod
    def touch(session_id, ip=None):
        patch = {"last_used_at": now_utc()}
        if ip:
            patch["ip"] = ip
        SessionRepository._coll().update_one({"_id": session_id}, {"$set": patch})

    @staticmethod
    def revoke(session_id, reason="logout"):
        SessionRepository._coll().update_one(
            {"_id": session_id, "revoked_at": None},
            {"$set": {"revoked_at": now_utc(), "revoke_reason": reason}},
        )

    @staticmethod
    def revoke_all_for_user(user_id, reason, except_ids=()):
        q = {"user_id": _as_oid(user_id), "revoked_at": None}
        if except_ids:
            q["_id"] = {"$nin": list(except_ids)}
        SessionRepository._coll().update_many(
            q, {"$set": {"revoked_at": now_utc(), "revoke_reason": reason}}
        )

    @staticmethod
    def rotate(old_doc, user_agent=None, ip=None):
        raw, new_doc = SessionRepository.issue(old_doc["user_id"], user_agent, ip)
        SessionRepository._coll().update_one(
            {"_id": old_doc["_id"]},
            {
                "$set": {
                    "revoked_at": now_utc(),
                    "revoke_reason": "rotated",
                    "rotated_to": new_doc["_id"],
                }
            },
        )
        return raw, new_doc
