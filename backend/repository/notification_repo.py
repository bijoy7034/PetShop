from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


class NotificationRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.NOTIFICATIONS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = NotificationRepository._coll()
        coll.create_index(
            [("user_id", ASCENDING), ("is_read", ASCENDING),
             ("created_at", DESCENDING)]
        )
        coll.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])

    @staticmethod
    def create(*, user_id, type, title, body=None, meta=None, link=None):
        now = now_utc()
        doc = {
            "user_id": user_id,
            "type": str(type),
            "title": title,
            "body": body,
            "meta": meta or {},
            "link": link,
            "is_read": False,
            "read_at": None,
            "created_at": now,
        }
        res = NotificationRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def bulk_create(rows):
        """Batch insert. Skip on empty list."""
        if not rows:
            return 0
        now = now_utc()
        docs = []
        for r in rows:
            docs.append({
                "user_id": r["user_id"],
                "type": str(r["type"]),
                "title": r["title"],
                "body": r.get("body"),
                "meta": r.get("meta") or {},
                "link": r.get("link"),
                "is_read": False,
                "read_at": None,
                "created_at": now,
            })
        res = NotificationRepository._coll().insert_many(docs)
        return len(res.inserted_ids)

    @staticmethod
    def list_for_user(user_id, *, unread_only=False, since=None,
                      page=1, page_size=50):
        q = {"user_id": user_id}
        if unread_only:
            q["is_read"] = False
        if since is not None:
            q["created_at"] = {"$gt": since}
        skip = (page - 1) * page_size
        cur = (
            NotificationRepository._coll()
            .find(q)
            .sort("created_at", DESCENDING)
            .skip(skip)
            .limit(page_size)
        )
        items = [to_public_doc(d) for d in cur]
        total = NotificationRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def unread_count(user_id):
        return NotificationRepository._coll().count_documents(
            {"user_id": user_id, "is_read": False}
        )

    @staticmethod
    def mark_read(notification_id, user_id):
        oid = oid_or_none(notification_id)
        if oid is None:
            return None
        now = now_utc()
        res = NotificationRepository._coll().update_one(
            # scope by user_id so a user can't flip another user's row
            {"_id": oid, "user_id": user_id},
            {"$set": {"is_read": True, "read_at": now}},
        )
        if not res.matched_count:
            return None
        return to_public_doc(
            NotificationRepository._coll().find_one({"_id": oid})
        )

    @staticmethod
    def mark_all_read(user_id):
        now = now_utc()
        res = NotificationRepository._coll().update_many(
            {"user_id": user_id, "is_read": False},
            {"$set": {"is_read": True, "read_at": now}},
        )
        return res.modified_count
