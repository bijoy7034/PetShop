from pymongo import DESCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc


class AuditRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.AUDIT_COLL]

    @staticmethod
    def ensure_indexes():
        AuditRepository._coll().create_index([("at", DESCENDING)])
        AuditRepository._coll().create_index([("actor_id", 1)])
        AuditRepository._coll().create_index([("resource_type", 1), ("resource_id", 1)])
        AuditRepository._coll().create_index([("action", 1)])

    @staticmethod
    def append(
        action,
        resource_type,
        resource_id=None,
        actor=None,
        before=None,
        after=None,
        request_id=None,
        ip=None,
    ):
        doc = {
            "action": str(action),
            "resource_type": str(resource_type),
            "resource_id": resource_id,
            "actor_id": (actor or {}).get("_id"),
            "actor_email": (actor or {}).get("email"),
            "actor_role": (actor or {}).get("role"),
            "before": before,
            "after": after,
            "at": now_utc(),
            "request_id": request_id,
            "ip": ip,
        }
        res = AuditRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return doc
