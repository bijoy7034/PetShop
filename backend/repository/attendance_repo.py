from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import to_public_doc


class AttendanceRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.ATTENDANCE_COLL]

    @staticmethod
    def ensure_indexes():
        coll = AttendanceRepository._coll()
        coll.create_index([("user_id", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("store_id", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("marked_at", DESCENDING)])

    @staticmethod
    def insert(*, user_id, user_name, store_id, store_name, lat, lng, distance_meters, notes):
        doc = {
            "user_id": user_id,
            "user_name": user_name,
            "store_id": store_id,
            "store_name": store_name,
            "lat": float(lat),
            "lng": float(lng),
            "distance_meters": float(distance_meters),
            "notes": notes,
            "marked_at": now_utc(),
        }
        res = AttendanceRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def list(user_id=None, store_id=None, skip=0, limit=50):
        q = {}
        if user_id:
            q["user_id"] = user_id
        if store_id:
            q["store_id"] = store_id
        cur = (
            AttendanceRepository._coll()
            .find(q)
            .sort("marked_at", DESCENDING)
            .skip(skip)
            .limit(limit)
        )
        items = [to_public_doc(d) for d in cur]
        total = AttendanceRepository._coll().count_documents(q)
        return items, total
