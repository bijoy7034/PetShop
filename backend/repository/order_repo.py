from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from enums.order import OrderStatus, PaymentStatus, payment_status_from
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


def _with_outstanding(doc):
    """to_public_doc + a computed outstanding balance for convenience."""
    out = to_public_doc(doc)
    if out is None:
        return None
    total = float(out.get("total") or 0)
    paid = float(out.get("amount_paid") or 0)
    out["outstanding"] = max(0.0, round(total - paid, 2))
    return out


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
        coll.create_index([("payment_status", ASCENDING), ("created_at", DESCENDING)])

    @staticmethod
    def by_id(order_id):
        oid = oid_or_none(order_id)
        if oid is None:
            return None
        return _with_outstanding(OrderRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def list(sales_rep_id=None, store_id=None, status=None, payment_status=None, skip=0, limit=50):
        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        if store_id:
            q["store_id"] = store_id
        if status:
            q["status"] = status
        if payment_status:
            q["payment_status"] = payment_status
        cur = (
            OrderRepository._coll()
            .find(q)
            .sort("created_at", DESCENDING)
            .skip(skip)
            .limit(limit)
        )
        items = [_with_outstanding(d) for d in cur]
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
            "payment_status": PaymentStatus.PENDING.value,
            "amount_paid": 0.0,
            "payment_history": [],
            "created_at": now,
            "updated_at": now,
        }
        res = OrderRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return _with_outstanding(doc)

    @staticmethod
    def record_payment(order_id, amount, method, notes, actor):
        """Atomically apply a payment. Refuses if the payment would push
        amount_paid past total. Returns None on refusal, the fresh doc on
        success."""
        oid = oid_or_none(order_id)
        if oid is None:
            return None
        amount = round(float(amount), 2)
        if amount <= 0:
            return None
        now = now_utc()
        event = {
            "amount": amount,
            "method": method,
            "notes": notes,
            "at": now,
            "by_user_id": (actor or {}).get("_id"),
            "by_user_name": (actor or {}).get("name"),
        }
        # Fetch, compute new totals, guard against overpayment inside the
        # update filter so a race can't tip amount_paid past total.
        doc = OrderRepository._coll().find_one({"_id": oid})
        if not doc:
            return None
        total = float(doc.get("total") or 0)
        current_paid = float(doc.get("amount_paid") or 0)
        new_paid = round(current_paid + amount, 2)
        if new_paid > total + 1e-6:
            return None
        new_status = payment_status_from(total, new_paid)
        res = OrderRepository._coll().update_one(
            {"_id": oid, "amount_paid": doc.get("amount_paid", 0.0)},
            {
                "$set": {
                    "amount_paid": new_paid,
                    "payment_status": new_status,
                    "updated_at": now,
                },
                "$push": {"payment_history": event},
            },
        )
        if res.matched_count == 0:
            # Another payment landed between our read and our write. Caller
            # can retry.
            return None
        return OrderRepository.by_id(order_id)

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
        return OrderRepository.by_id(order_id)  # noqa: uses _with_outstanding

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
