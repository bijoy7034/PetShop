from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from enums.order import OrderStatus, PaymentStatus, payment_status_from
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc
from repository.counter_repo import next_order_code


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
        coll.create_index([("last_status_updated_at", DESCENDING)])
        coll.create_index([("store_district", ASCENDING), ("created_at", DESCENDING)])
        coll.create_index([("payment_due_date", ASCENDING)])

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
    def insert(
        *,
        store,
        sales_rep,
        lines,
        total,
        notes,
        status=OrderStatus.PLACED.value,
        expected_delivery_date=None,
    ):
        """Insert an order and snapshot every store field that could later
        change (name, address, district, credit terms, cancellation policy)
        so historical orders stay accurate no matter what happens to the
        store record."""
        now = now_utc()
        contact = store.get("contact") or {}
        geo = store.get("geo") or {}
        delivery_snapshot = {
            "name": store.get("name"),
            "location": store.get("location"),
            "district": store.get("district"),
            "gst_number": store.get("gst_number"),
            "geo_lat": geo.get("lat"),
            "geo_lng": geo.get("lng"),
            "contact_name": contact.get("name"),
            "contact_phone": contact.get("phone"),
            "contact_email": contact.get("email"),
        }
        doc = {
            "code": next_order_code(),
            "status": status,
            "last_status_updated_at": now,
            "store_id": store["_id"],
            "store_code": store.get("code"),
            "store_name": store.get("name"),
            "store_district": store.get("district"),
            "delivery_address_snapshot": delivery_snapshot,
            "sales_rep_id": sales_rep["_id"],
            "sales_rep_name": sales_rep.get("name"),
            "lines": lines,
            "total": float(total),
            "notes": notes,
            "history": [
                {
                    "status": status,
                    "at": now,
                    "by_user_id": sales_rep["_id"],
                    "by_user_name": sales_rep.get("name"),
                    "note": None,
                }
            ],
            "expected_delivery_date": expected_delivery_date,
            "delivered_at": None,
            "cancel_reason": None,
            "rejection_reason": None,
            "delay_reason": None,
            "accepted_by_id": None,
            "accepted_by_name": None,
            "payment_status": PaymentStatus.PENDING.value,
            "amount_paid": 0.0,
            "payment_history": [],
            "credit_period_days": int(store.get("credit_period_days") or 30),
            "payment_due_date": None,
            "is_free_cancellation": bool(store.get("is_free_cancellation", True)),
            "cancellation_charges": float(store.get("cancellation_charges") or 0.0),
            "return_window_days": int(store.get("return_window_days") or 7),
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
    def set_status(order_id, new_status, actor, note=None, extra=None):
        """Append a history event and set the top-level status. Also stamps
        last_status_updated_at so reports can sort by 'recently changed'.
        `extra` is merged into the top-level $set for status-specific
        fields (accepted_by, delivered_at, delay_reason, ...)."""
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
        set_doc = {
            "status": new_status,
            "last_status_updated_at": now,
            "updated_at": now,
        }
        if extra:
            set_doc.update(extra)
        OrderRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": set_doc,
                "$push": {"history": event},
            },
        )
        return OrderRepository.by_id(order_id)

    @staticmethod
    def update_lines(order_id, new_lines, new_total, actor, note=None, log_edit=True):
        """Replace lines/total. If `log_edit` is True, also append an
        'edited' history event so the office UI can render "Order edited
        by <actor>" in the timeline. Callers accepting at placed qty
        (no reduction) pass log_edit=False to avoid a noisy log entry."""
        oid = oid_or_none(order_id)
        if oid is None:
            return None
        now = now_utc()
        update = {
            "$set": {
                "lines": new_lines,
                "total": float(new_total),
                "updated_at": now,
            },
        }
        if log_edit:
            update["$push"] = {
                "history": {
                    "status": "edited",
                    "at": now,
                    "by_user_id": (actor or {}).get("_id"),
                    "by_user_name": (actor or {}).get("name"),
                    "note": note,
                }
            }
        OrderRepository._coll().update_one({"_id": oid}, update)
        return OrderRepository.by_id(order_id)

    @staticmethod
    def cancel(order_id, reason, actor):
        return OrderRepository.set_status(
            order_id,
            OrderStatus.CANCELLED.value,
            actor,
            note=reason,
            extra={"cancel_reason": reason},
        )

    @staticmethod
    def admin_reject(order_id, reason, actor):
        """Admin denies a pending_admin_approval order — status transitions
        to cancelled and rejection_reason is recorded (separate from
        sales-rep cancellation for reporting)."""
        return OrderRepository.set_status(
            order_id,
            OrderStatus.CANCELLED.value,
            actor,
            note=reason,
            extra={"rejection_reason": reason},
        )

    @staticmethod
    def mark_delayed(order_id, reason, actor):
        return OrderRepository.set_status(
            order_id,
            OrderStatus.DELAYED.value,
            actor,
            note=reason,
            extra={"delay_reason": reason},
        )

    @staticmethod
    def mark_accepted(order_id, actor, note=None):
        return OrderRepository.set_status(
            order_id,
            OrderStatus.ACCEPTED.value,
            actor,
            note=note,
            extra={
                "accepted_by_id": (actor or {}).get("_id"),
                "accepted_by_name": (actor or {}).get("name"),
                # Clear any prior delay_reason once the order resumes.
                "delay_reason": None,
            },
        )

    @staticmethod
    def mark_delivered(order_id, actor, note=None):
        """Also stamps delivered_at and computes payment_due_date from the
        snapshotted credit_period_days."""
        from datetime import timedelta
        doc = OrderRepository._coll().find_one(
            {"_id": oid_or_none(order_id)},
            {"credit_period_days": 1},
        )
        credit_days = int((doc or {}).get("credit_period_days") or 30)
        now = now_utc()
        due = now + timedelta(days=credit_days)
        return OrderRepository.set_status(
            order_id,
            OrderStatus.DELIVERED.value,
            actor,
            note=note,
            extra={"delivered_at": now, "payment_due_date": due},
        )
