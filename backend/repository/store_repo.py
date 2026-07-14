from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from enums.store import StoreStatus
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc
from repository.counter_repo import next_store_code


class StoreRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.STORES_COLL]

    @staticmethod
    def ensure_indexes():
        coll = StoreRepository._coll()
        coll.create_index([("sales_rep_id", ASCENDING)])
        coll.create_index([("status", ASCENDING)])
        coll.create_index([("name", ASCENDING)])

    @staticmethod
    def by_id(store_id):
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        return to_public_doc(StoreRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def list(sales_rep_id=None, status=None, search=None, skip=0, limit=50):
        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        if status:
            q["status"] = status
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"location": {"$regex": search, "$options": "i"}},
                {"gst_number": {"$regex": search, "$options": "i"}},
            ]
        cur = (
            StoreRepository._coll()
            .find(q)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        items = [to_public_doc(d) for d in cur]
        total = StoreRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def insert(
        *,
        sales_rep_id,
        sales_rep_name,
        name,
        location,
        contact,
        geo,
        email,
        gst_number,
        notes,
        status=StoreStatus.PENDING.value,
        credit_limit=0.0,
    ):
        now = now_utc()
        doc = {
            "code": next_store_code(),
            "name": name,
            "location": location,
            "contact": contact,
            "geo": geo,
            "email": email,
            "gst_number": gst_number,
            "notes": notes,
            "sales_rep_id": sales_rep_id,
            "sales_rep_name": sales_rep_name,
            "status": status,
            "credit_limit": float(credit_limit or 0.0),
            "credit_used": 0.0,
            "reject_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        res = StoreRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def assign(store_id, sales_rep_id, sales_rep_name):
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        StoreRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": {
                    "sales_rep_id": sales_rep_id,
                    "sales_rep_name": sales_rep_name,
                    "updated_at": now_utc(),
                }
            },
        )
        return StoreRepository.by_id(store_id)

    @staticmethod
    def update(store_id, patch):
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return StoreRepository.by_id(store_id)
        patch["updated_at"] = now_utc()
        StoreRepository._coll().update_one({"_id": oid}, {"$set": patch})
        return StoreRepository.by_id(store_id)

    @staticmethod
    def approve(store_id, credit_limit):
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        StoreRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": StoreStatus.APPROVED.value,
                    "credit_limit": float(credit_limit),
                    "reject_reason": None,
                    "updated_at": now_utc(),
                }
            },
        )
        return StoreRepository.by_id(store_id)

    @staticmethod
    def reject(store_id, reason):
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        StoreRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": StoreStatus.REJECTED.value,
                    "reject_reason": reason,
                    "updated_at": now_utc(),
                }
            },
        )
        return StoreRepository.by_id(store_id)

    @staticmethod
    def delete(store_id):
        oid = oid_or_none(store_id)
        if oid is None:
            return False
        res = StoreRepository._coll().delete_one({"_id": oid})
        return res.deleted_count == 1

    @staticmethod
    def adjust_credit_used(store_id, delta):
        """Atomically bump credit_used. Refuses to go below zero (that would
        mean releasing more credit than a store ever consumed — a bug)."""
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        q = {"_id": oid}
        if delta < 0:
            q["credit_used"] = {"$gte": -delta}
        res = StoreRepository._coll().update_one(
            q,
            {"$inc": {"credit_used": float(delta)}, "$set": {"updated_at": now_utc()}},
        )
        if res.matched_count == 0:
            return None
        return StoreRepository.by_id(store_id)
