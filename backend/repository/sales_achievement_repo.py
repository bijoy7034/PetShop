from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


class SalesAchievementRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.SALES_ACHIEVEMENTS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = SalesAchievementRepository._coll()
        coll.create_index([("is_active", ASCENDING), ("start_date", ASCENDING), ("end_date", ASCENDING)])
        coll.create_index([("period", ASCENDING)])
        coll.create_index([("created_at", DESCENDING)])

    @staticmethod
    def by_id(achievement_id):
        oid = oid_or_none(achievement_id)
        if oid is None:
            return None
        return to_public_doc(SalesAchievementRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def list(is_active=None, period=None, skip=0, limit=50):
        q = {}
        if is_active is not None:
            q["is_active"] = bool(is_active)
        if period:
            q["period"] = period
        cur = (
            SalesAchievementRepository._coll()
            .find(q)
            .sort("created_at", DESCENDING)
            .skip(skip)
            .limit(limit)
        )
        items = [to_public_doc(d) for d in cur]
        total = SalesAchievementRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def list_active_at(dt):
        """All achievements active at the given datetime. Used by the
        progress endpoint to know which achievement rows to hydrate for
        a rep."""
        q = {
            "is_active": True,
            "start_date": {"$lte": dt},
            "end_date": {"$gte": dt},
        }
        return [to_public_doc(d) for d in SalesAchievementRepository._coll().find(q)]

    @staticmethod
    def insert(*, title, description, reward, period, start_date, end_date,
               target, is_active, actor):
        now = now_utc()
        doc = {
            "title": title,
            "description": description,
            "reward": reward,
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "target": target,
            "is_active": bool(is_active),
            "created_by_id": (actor or {}).get("_id"),
            "created_by_name": (actor or {}).get("name"),
            "created_at": now,
            "updated_at": now,
        }
        res = SalesAchievementRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def update(achievement_id, patch):
        oid = oid_or_none(achievement_id)
        if oid is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return SalesAchievementRepository.by_id(achievement_id)
        patch["updated_at"] = now_utc()
        SalesAchievementRepository._coll().update_one({"_id": oid}, {"$set": patch})
        return SalesAchievementRepository.by_id(achievement_id)

    @staticmethod
    def delete(achievement_id):
        oid = oid_or_none(achievement_id)
        if oid is None:
            return False
        res = SalesAchievementRepository._coll().delete_one({"_id": oid})
        return res.deleted_count == 1
