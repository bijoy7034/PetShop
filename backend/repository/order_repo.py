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
    def count_by_status(status, *, sales_rep_id=None):
        q = {"status": status}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        return OrderRepository._coll().count_documents(q)

    @staticmethod
    def count_by_statuses(statuses, *, sales_rep_id=None):
        q = {"status": {"$in": list(statuses)}}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        return OrderRepository._coll().count_documents(q)

    @staticmethod
    def revenue_for_month(year, month, *, sales_rep_id=None):
        """Sum of order.total for orders CREATED in the given month
        whose status is 'accepted+' (revenue actually booked)."""
        from datetime import datetime, timedelta, timezone
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = (datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
               if month < 12 else datetime(year + 1, 1, 1, tzinfo=timezone.utc))
        end -= timedelta(microseconds=1)
        counted = ("accepted", "packing", "out_for_delivery", "delivered")
        q = {
            "status": {"$in": list(counted)},
            "created_at": {"$gte": start, "$lte": end},
        }
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        cur = OrderRepository._coll().aggregate([
            {"$match": q},
            {"$group": {"_id": None, "revenue": {"$sum": "$total"}}},
        ])
        rows = list(cur)
        return float(rows[0]["revenue"]) if rows else 0.0

    @staticmethod
    def delivered_count_for_month(year, month, *, sales_rep_id=None):
        from datetime import datetime, timedelta, timezone
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = (datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
               if month < 12 else datetime(year + 1, 1, 1, tzinfo=timezone.utc))
        end -= timedelta(microseconds=1)
        q = {
            "status": "delivered",
            "delivered_at": {"$gte": start, "$lte": end},
        }
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        return OrderRepository._coll().count_documents(q)

    @staticmethod
    def distinct_stores_reached():
        """Distinct store_ids across every counted order (proxy for
        'stores that actually did business with us')."""
        counted = ("accepted", "packing", "out_for_delivery", "delivered")
        return len(OrderRepository._coll().distinct(
            "store_id", {"status": {"$in": list(counted)}}
        ))

    @staticmethod
    def stats(*, sales_rep_id=None, store_id=None):
        """Dashboard aggregation: total orders, total volume, counts by
        status and by payment_status. Every status/payment_status key
        from the enum is present in the response, zero-filled — the
        frontend can render the full grid without guarding against
        missing keys."""
        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        if store_id:
            q["store_id"] = store_id
        pipeline = [
            {"$match": q},
            {"$facet": {
                "totals": [
                    {"$group": {
                        "_id": None,
                        "total_orders": {"$sum": 1},
                        "total_volume": {"$sum": "$total"},
                    }},
                ],
                "by_status": [
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                ],
                "by_payment": [
                    {"$group": {"_id": "$payment_status", "count": {"$sum": 1}}},
                ],
            }},
        ]
        res = list(OrderRepository._coll().aggregate(pipeline))[0]
        totals = res["totals"][0] if res["totals"] else {"total_orders": 0, "total_volume": 0.0}

        counts_by_status = {s.value: 0 for s in OrderStatus}
        for row in res["by_status"]:
            counts_by_status[row["_id"]] = int(row["count"])

        counts_by_payment_status = {s.value: 0 for s in PaymentStatus}
        for row in res["by_payment"]:
            if row["_id"]:
                counts_by_payment_status[row["_id"]] = int(row["count"])

        return {
            "total_orders": int(totals["total_orders"] or 0),
            "total_volume": float(totals["total_volume"] or 0),
            "counts_by_status": counts_by_status,
            "counts_by_payment_status": counts_by_payment_status,
        }

    @staticmethod
    def overdue_aggregate(store_ids=None, *, now=None):
        """Sum of outstanding across every unpaid order past
        payment_due_date, optionally scoped to a set of store ids.
        Returns {overdue_store_ids: set(...), total_overdue_amount: float}."""
        now = now or now_utc()
        q = {
            "payment_due_date": {"$lt": now},
            "payment_status": {"$ne": "paid"},
        }
        if store_ids is not None:
            q["store_id"] = {"$in": list(store_ids)}
        pipeline = [
            {"$match": q},
            {"$project": {
                "store_id": 1,
                "outstanding": {"$max": [
                    0, {"$subtract": [
                        {"$ifNull": ["$total", 0]},
                        {"$ifNull": ["$amount_paid", 0]},
                    ]}
                ]},
            }},
            {"$group": {
                "_id": "$store_id",
                "store_overdue": {"$sum": "$outstanding"},
            }},
        ]
        overdue_by_store = {r["_id"]: float(r["store_overdue"] or 0)
                            for r in OrderRepository._coll().aggregate(pipeline)}
        # Only stores whose overdue sum > 0.
        overdue_by_store = {k: v for k, v in overdue_by_store.items() if v > 0}
        return {
            "overdue_by_store": overdue_by_store,
            "total_overdue_amount": round(sum(overdue_by_store.values()), 2),
        }

    @staticmethod
    def list(sales_rep_id=None, store_id=None, status=None, payment_status=None,
             over_credit_approved=None, skip=0, limit=50):
        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        if store_id:
            q["store_id"] = store_id
        if status:
            q["status"] = status
        if payment_status:
            q["payment_status"] = payment_status
        if over_credit_approved is True:
            q["over_credit_approved"] = True
        elif over_credit_approved is False:
            q["over_credit_approved"] = {"$ne": True}
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
        sibling_order_id=None,
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
            "sibling_order_id": sibling_order_id,
            "ready_to_submit_at": None,
            "created_at": now,
            "updated_at": now,
        }
        res = OrderRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return _with_outstanding(doc)

    @staticmethod
    def set_sibling(order_id, sibling_id):
        """Cross-link two sibling orders after both have been inserted."""
        oid = oid_or_none(order_id)
        if oid is None:
            return None
        OrderRepository._coll().update_one(
            {"_id": oid},
            {"$set": {"sibling_order_id": sibling_id, "updated_at": now_utc()}},
        )
        return OrderRepository.by_id(order_id)

    @staticmethod
    def find_waiting_for_stock():
        """Every order currently held for stock, FIFO. Used by the
        auto-promotion scan when stock arrives."""
        cur = OrderRepository._coll().find(
            {"status": OrderStatus.WAITING_FOR_STOCK.value}
        ).sort("created_at", 1)
        return [_with_outstanding(d) for d in cur]

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
    def find_overdue_for_notification(*, now, dedupe_hours=168):
        """Orders that are past payment_due_date, not fully paid, and
        weren't notified about being overdue in the last `dedupe_hours`
        (default 168 = 7 days). Used by the scheduler."""
        from datetime import timedelta
        cutoff = now - timedelta(hours=int(dedupe_hours))
        q = {
            "payment_due_date": {"$lt": now},
            "payment_status": {"$ne": "paid"},
            "outstanding": {"$gt": 0},
            "$or": [
                {"overdue_notified_at": {"$exists": False}},
                {"overdue_notified_at": {"$lt": cutoff}},
            ],
        }
        return [_with_outstanding(d) for d in OrderRepository._coll().find(q)]

    @staticmethod
    def mark_overdue_notified(order_id):
        oid = oid_or_none(order_id)
        if oid is None:
            return
        OrderRepository._coll().update_one(
            {"_id": oid}, {"$set": {"overdue_notified_at": now_utc()}},
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
