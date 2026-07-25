from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import to_public_doc


_ALLOWED_SORT_FIELDS = {"at", "action", "resource_type", "actor_email"}


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
            "actor_name": (actor or {}).get("name"),
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

    @staticmethod
    def list(*, action=None, resource_type=None, resource_id=None,
             actor_id=None, actor_email=None, ip=None,
             from_dt=None, to_dt=None, search=None,
             sort_by="at", sort_dir="desc",
             skip=0, limit=50):
        """Filtered + sorted list of audit events. Sort direction is
        capped to the whitelisted fields so a caller can't sort on
        `before`/`after` blobs. Default sort: most recent first."""
        q = {}
        if action:
            q["action"] = action
        if resource_type:
            q["resource_type"] = resource_type
        if resource_id:
            q["resource_id"] = resource_id
        if actor_id:
            q["actor_id"] = actor_id
        if actor_email:
            q["actor_email"] = {"$regex": actor_email, "$options": "i"}
        if ip:
            q["ip"] = ip
        if from_dt or to_dt:
            q["at"] = {}
            if from_dt:
                q["at"]["$gte"] = from_dt
            if to_dt:
                q["at"]["$lte"] = to_dt
        if search:
            # Cross-field text match across the most useful fields.
            q["$or"] = [
                {"action": {"$regex": search, "$options": "i"}},
                {"resource_type": {"$regex": search, "$options": "i"}},
                {"actor_email": {"$regex": search, "$options": "i"}},
                {"actor_name": {"$regex": search, "$options": "i"}},
            ]

        field = sort_by if sort_by in _ALLOWED_SORT_FIELDS else "at"
        direction = ASCENDING if sort_dir == "asc" else DESCENDING

        cur = (
            AuditRepository._coll()
            .find(q)
            .sort(field, direction)
            .skip(skip)
            .limit(limit)
        )
        items = [to_public_doc(d) for d in cur]
        total = AuditRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def distinct_actions():
        """For populating the frontend filter dropdown."""
        return sorted(AuditRepository._coll().distinct("action"))

    @staticmethod
    def distinct_resource_types():
        return sorted(AuditRepository._coll().distinct("resource_type"))
