from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from enums.order import OrderStatus
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


class OrderRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.ORDERS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = OrderRepository._coll()
        coll.create_index([("store_id", ASCENDING), ("created_at", DESCENDING)])
        coll.create_index([("sales_rep_id", ASCENDING), ("created_at", DESCENDING)])
        coll.create_index([("status", ASCENDING), ("created_at", DESCENDING)])

    @staticmethod
    def by_id(order_id):
        oid = oid_or_none(order_id)
        if oid is None:
            return None
        return to_public_doc(OrderRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def list(sales_rep_id=None, store_id=None, status=None, skip=0, limit=50):
        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        if store_id:
            q["store_id"] = store_id
        if status:
            q["status"] = status
        cur = (
            OrderRepository._coll()
            .find(q)
            .sort("created_at", DESCENDING)
            .skip(skip)
            .limit(limit)
        )
        items = [to_public_doc(d) for d in cur]
        total = OrderRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def insert(*, store, sales_rep, lines, total, notes):
        now = now_utc()
        doc = {
            "store_id": store["_id"],
            "store_name": store.get("name"),
            "sales_rep_id": sales_rep["_id"],
            "sales_rep_name": sales_rep.get("name"),
            "status": OrderStatus.PLACED.value,
            "lines": lines,
            "total": float(total),
            "notes": notes,
            "history": [
                {
                    "status": OrderStatus.PLACED.value,
                    "at": now,
                    "by_user_id": sales_rep["_id"],
                    "by_user_name": sales_rep.get("name"),
                    "note": None,
                }
            ],
            "cancel_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        res = OrderRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def set_status(order_id, new_status, actor, note=None):
        """Append a history event and set the top-level status. Caller is
        responsible for validating the transition."""
        oid = oid_or_none(order_id)
        if oid is None:
            return None
        now = now_utc()
        event = {
            "status": new_status,
            "at": now,
            "by_user_id": (actor or {}).get("_id"),
            "by_user_name": (actor or {}).get("name"),
            "note": note,
        }
        OrderRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": {"status": new_status, "updated_at": now},
                "$push": {"history": event},
            },
        )
        return OrderRepository.by_id(order_id)

    @staticmethod
    def cancel(order_id, reason, actor):
        oid = oid_or_none(order_id)
        if oid is None:
            return None
        now = now_utc()
        event = {
            "status": OrderStatus.CANCELLED.value,
            "at": now,
            "by_user_id": (actor or {}).get("_id"),
            "by_user_name": (actor or {}).get("name"),
            "note": reason,
        }
        OrderRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": OrderStatus.CANCELLED.value,
                    "cancel_reason": reason,
                    "updated_at": now,
                },
                "$push": {"history": event},
            },
        )
        return OrderRepository.by_id(order_id)
