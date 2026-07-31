from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from enums.store import CreditChangeStatus, StoreStatus
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc
from repository.counter_repo import next_store_code


def _with_credit_state(doc):
    """Attach derived credit-exposure fields to a Store dict. Kept out
    of the stored doc so it can't drift from credit_limit/credit_used."""
    if not doc:
        return doc
    limit = float(doc.get("credit_limit") or 0)
    used = float(doc.get("credit_used") or 0)
    doc["available_credit"] = round(limit - used, 2)
    doc["is_over_credit_limit"] = used > limit + 1e-6
    return doc


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
        return _with_credit_state(
            to_public_doc(StoreRepository._coll().find_one({"_id": oid}))
        )

    @staticmethod
    def rep_summary(*, rep_id=None):
        """Assigned-store counts + credit metrics for a rep's book of
        business. If `rep_id` is None, returns the same aggregation
        across every store (admin/office view)."""
        q = {}
        if rep_id:
            q["sales_rep_id"] = rep_id
        pipeline = [
            {"$match": q},
            {"$facet": {
                "counts": [
                    {"$group": {
                        "_id": "$status",
                        "count": {"$sum": 1},
                    }},
                ],
                "totals": [
                    {"$group": {
                        "_id": None,
                        "total_assigned_stores": {"$sum": 1},
                        "total_credit_limit": {"$sum": {"$ifNull": ["$credit_limit", 0]}},
                        "total_utilized_credit": {"$sum": {"$ifNull": ["$credit_used", 0]}},
                    }},
                ],
                "store_ids": [
                    {"$project": {"_id": 1}},
                ],
            }},
        ]
        res = list(StoreRepository._coll().aggregate(pipeline))[0]
        totals = res["totals"][0] if res["totals"] else {
            "total_assigned_stores": 0, "total_credit_limit": 0.0,
            "total_utilized_credit": 0.0,
        }

        status_counts = {"pending": 0, "approved": 0, "rejected": 0}
        for row in res["counts"]:
            if row["_id"] in status_counts:
                status_counts[row["_id"]] = int(row["count"])

        store_ids = [str(r["_id"]) for r in res["store_ids"]]

        # Overdue is a per-order concept — pull the aggregate from orders.
        from repository.order_repo import OrderRepository
        overdue = OrderRepository.overdue_aggregate(store_ids=store_ids)

        credit_limit = float(totals["total_credit_limit"] or 0)
        credit_used = float(totals["total_utilized_credit"] or 0)
        return {
            "total_assigned_stores": int(totals["total_assigned_stores"] or 0),
            "approved_stores_count": status_counts["approved"],
            "pending_stores_count": status_counts["pending"],
            "rejected_stores_count": status_counts["rejected"],
            "credit_metrics": {
                "total_credit_limit": round(credit_limit, 2),
                "total_utilized_credit": round(credit_used, 2),
                "total_available_credit": round(credit_limit - credit_used, 2),
                "overdue_stores_count": len(overdue["overdue_by_store"]),
                "total_overdue_amount": overdue["total_overdue_amount"],
            },
        }

    @staticmethod
    def credit_summary(*, district=None, search=None):
        """Portfolio credit view — total exposure, overdue, and a
        breakdown of the ways a store can be in violation:
          - limit_exceeded: credit_used > credit_limit but no overdue
          - period_overdue: has overdue payments but within credit limit
          - both_exceeded: both conditions true at once
        Only approved stores contribute (pending/rejected have no
        credit exposure)."""
        q = {"status": "approved"}
        if district:
            q["district"] = district
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"location": {"$regex": search, "$options": "i"}},
                {"gst_number": {"$regex": search, "$options": "i"}},
            ]

        pipeline = [
            {"$match": q},
            {"$facet": {
                "totals": [
                    {"$group": {
                        "_id": None,
                        "total_customers": {"$sum": 1},
                        "total_credit_limit_sum": {"$sum": {"$ifNull": ["$credit_limit", 0]}},
                        "total_outstanding_balance": {"$sum": {"$ifNull": ["$credit_used", 0]}},
                    }},
                ],
                "limit_exceeded_ids": [
                    {"$match": {
                        "$expr": {"$gt": [
                            {"$ifNull": ["$credit_used", 0]},
                            {"$ifNull": ["$credit_limit", 0]},
                        ]}
                    }},
                    {"$project": {"_id": 1}},
                ],
                "store_ids": [
                    {"$project": {"_id": 1}},
                ],
            }},
        ]
        res = list(StoreRepository._coll().aggregate(pipeline))[0]
        totals = res["totals"][0] if res["totals"] else {
            "total_customers": 0,
            "total_credit_limit_sum": 0.0,
            "total_outstanding_balance": 0.0,
        }

        limit_exceeded_ids = {str(r["_id"]) for r in res["limit_exceeded_ids"]}
        store_ids = [str(r["_id"]) for r in res["store_ids"]]

        from repository.order_repo import OrderRepository
        overdue = OrderRepository.overdue_aggregate(store_ids=store_ids)
        overdue_ids = set(overdue["overdue_by_store"].keys())

        both = limit_exceeded_ids & overdue_ids
        limit_only = limit_exceeded_ids - overdue_ids
        overdue_only = overdue_ids - limit_exceeded_ids

        credit_sum = float(totals["total_credit_limit_sum"] or 0)
        outstanding = float(totals["total_outstanding_balance"] or 0)

        return {
            "total_customers": int(totals["total_customers"] or 0),
            "total_credit_limit_sum": round(credit_sum, 2),
            "total_outstanding_balance": round(outstanding, 2),
            "total_available_credit": round(credit_sum - outstanding, 2),
            "total_overdue_amount": overdue["total_overdue_amount"],
            "violating_stores_count": len(limit_exceeded_ids | overdue_ids),
            "violations_breakdown": {
                "limit_exceeded_count": len(limit_only),
                "period_overdue_count": len(overdue_only),
                "both_exceeded_count": len(both),
            },
        }

    @staticmethod
    def credit_violations_report(*, district=None, search=None):
        """Detailed row-per-store list of every store in credit
        violation — either over its limit, has overdue payments, or
        both. Includes a computed health_score (0-100, higher is
        better) so the frontend can sort/rank the queue."""
        from repository.order_repo import OrderRepository

        q = {"status": "approved"}
        if district:
            q["district"] = district
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"location": {"$regex": search, "$options": "i"}},
                {"gst_number": {"$regex": search, "$options": "i"}},
            ]
        approved_stores = list(StoreRepository._coll().find(q))
        store_ids = [str(s["_id"]) for s in approved_stores]
        overdue = OrderRepository.overdue_aggregate(store_ids=store_ids)
        overdue_by_store = overdue["overdue_by_store"]

        rows = []
        for s in approved_stores:
            limit = float(s.get("credit_limit") or 0)
            used = float(s.get("credit_used") or 0)
            sid = str(s["_id"])
            overdue_amount = float(overdue_by_store.get(sid, 0))
            is_over_limit = used > limit + 1e-6
            is_overdue = overdue_amount > 0
            if not (is_over_limit or is_overdue):
                continue

            if is_over_limit and is_overdue:
                vtype = "both_exceeded"
            elif is_over_limit:
                vtype = "limit_exceeded"
            else:
                vtype = "period_overdue"

            # Health score: 100 baseline; over-limit and overdue each
            # dock a proportional slice (up to 50 each).
            health = 100.0
            if is_over_limit and limit > 0:
                over_pct = (used - limit) / limit * 100
                health -= min(50.0, over_pct)
            if is_overdue:
                overdue_pct = overdue_amount / max(1.0, used) * 100
                health -= min(50.0, overdue_pct)

            rows.append({
                "store_id": sid,
                "store_code": s.get("code"),
                "store_name": s.get("name"),
                "district": s.get("district"),
                "sales_rep_id": s.get("sales_rep_id"),
                "sales_rep_name": s.get("sales_rep_name"),
                "credit_limit": round(limit, 2),
                "outstanding_balance": round(used, 2),
                "available_credit": round(limit - used, 2),
                "overdue_amount": round(overdue_amount, 2),
                "violation_type": vtype,
                "health_score": int(max(0, health)),
            })
        rows.sort(key=lambda r: (r["health_score"], -r["overdue_amount"]))
        return {
            "total_violating_stores": len(rows),
            "total_overdue_sum": round(sum(r["overdue_amount"] for r in rows), 2),
            "stores": rows,
        }

    @staticmethod
    def store_credit_report(store_id, *, start_date=None, end_date=None):
        """Per-store credit statement + interleaved transaction log.
        Every order create is an `order_invoice` event; every recorded
        payment is a `payment_received` event. Events are sorted by
        date and stamped with the running outstanding balance after
        the event."""
        from repository.order_repo import OrderRepository

        store = StoreRepository.by_id(store_id)
        if not store:
            return None

        # Pull every order for the store — orders repo already has
        # `list` with a store_id filter, but we need everything, not
        # paginated. Query directly.
        oq = {"store_id": store_id}
        if start_date or end_date:
            oq["created_at"] = {}
            if start_date:
                oq["created_at"]["$gte"] = start_date
            if end_date:
                oq["created_at"]["$lte"] = end_date
        orders = list(
            OrderRepository._coll().find(oq).sort("created_at", 1)
        )

        # Build event list.
        events = []
        for o in orders:
            oid = str(o["_id"])
            events.append({
                "transaction_id": f"invoice:{oid}",
                "date": o.get("created_at"),
                "type": "order_invoice",
                "reference_code": o.get("code"),
                "amount": float(o.get("total") or 0),
                "sign": +1,   # increases outstanding
                "status": o.get("status"),
                "payment_status": o.get("payment_status"),
            })
            for i, p in enumerate(o.get("payment_history") or []):
                events.append({
                    "transaction_id": f"payment:{oid}:{i}",
                    "date": p.get("at"),
                    "type": "payment_received",
                    "reference_code": p.get("method") or o.get("code"),
                    "amount": float(p.get("amount") or 0),
                    "sign": -1,
                    "note": p.get("notes"),
                })
        # Filter events by date range too (payments may fall outside
        # the order's own created_at window).
        if start_date or end_date:
            events = [
                e for e in events
                if (not start_date or e["date"] >= start_date)
                and (not end_date or e["date"] <= end_date)
            ]
        events.sort(key=lambda e: (e["date"] or e["transaction_id"]))

        # Compute running balance.
        balance = 0.0
        out_events = []
        for e in events:
            balance += e["sign"] * e["amount"]
            out_events.append({
                "transaction_id": e["transaction_id"],
                "date": e["date"],
                "type": e["type"],
                "reference_code": e["reference_code"],
                "amount": round(e["amount"], 2),
                "balance_after": round(max(0.0, balance), 2),
            })

        # Live snapshot (from the store doc + overdue).
        overdue = OrderRepository.overdue_aggregate(store_ids=[store_id])
        overdue_amount = float(overdue["overdue_by_store"].get(store_id, 0))

        return {
            "store_id": store_id,
            "store_code": store.get("code"),
            "store_name": store.get("name"),
            "district": store.get("district"),
            "sales_rep_name": store.get("sales_rep_name"),
            "credit_limit": float(store.get("credit_limit") or 0),
            "outstanding_balance": float(store.get("credit_used") or 0),
            "available_credit": store.get("available_credit", 0),
            "overdue_amount": round(overdue_amount, 2),
            "transactions": out_events,
        }

    @staticmethod
    def store_info_dashboard(*, sales_rep_id=None):
        """One tile per store the rep is assigned to (or every store
        for office/admin). Each tile carries credit + per-status order
        counts computed in a single aggregation over the orders
        collection — no per-store round-trips."""
        from repository.order_repo import OrderRepository

        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        stores = list(StoreRepository._coll().find(q))
        store_ids = [str(s["_id"]) for s in stores]
        if not store_ids:
            return {"total_stores": 0, "stores": []}

        # Group orders by (store_id, status) in one pass.
        pipeline = [
            {"$match": {"store_id": {"$in": store_ids}}},
            {"$group": {
                "_id": {"store": "$store_id", "status": "$status"},
                "count": {"$sum": 1},
            }},
        ]
        counts_map = {}
        for row in OrderRepository._coll().aggregate(pipeline):
            k = row["_id"]
            counts_map.setdefault(k["store"], {})[k["status"]] = int(row["count"])

        statuses = (
            "placed", "accepted", "packing", "out_for_delivery",
            "delivered", "waiting_for_stock", "ready_to_submit",
            "pending_admin_approval", "delayed", "cancelled",
        )
        tiles = []
        for s in stores:
            sid = str(s["_id"])
            limit = float(s.get("credit_limit") or 0)
            used = float(s.get("credit_used") or 0)
            per = counts_map.get(sid, {})
            tiles.append({
                "id": sid,
                "code": s.get("code"),
                "name": s.get("name"),
                "district": s.get("district"),
                "credit_limit": round(limit, 2),
                "credit_utilized": round(used, 2),
                "available_credit": round(limit - used, 2),
                "is_over_credit_limit": used > limit + 1e-6,
                "order_counts": {st: int(per.get(st, 0)) for st in statuses},
            })
        tiles.sort(key=lambda t: t["name"] or "")
        return {"total_stores": len(tiles), "stores": tiles}

    @staticmethod
    def over_credit_report(*, district=None, search=None):
        """Every approved store where credit_used > credit_limit OR
        available_credit < 0 — the same condition, both phrasings.
        Includes the excess amount so the frontend can rank."""
        q = {"status": "approved"}
        if district:
            q["district"] = district
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"location": {"$regex": search, "$options": "i"}},
                {"code": {"$regex": search, "$options": "i"}},
            ]
        pipeline = [
            {"$match": q},
            {"$match": {"$expr": {"$gt": [
                {"$ifNull": ["$credit_used", 0]},
                {"$ifNull": ["$credit_limit", 0]},
            ]}}},
            {"$sort": {"credit_used": -1}},
        ]
        rows = []
        for s in StoreRepository._coll().aggregate(pipeline):
            limit = float(s.get("credit_limit") or 0)
            used = float(s.get("credit_used") or 0)
            rows.append({
                "store_id": str(s["_id"]),
                "store_code": s.get("code"),
                "store_name": s.get("name"),
                "district": s.get("district"),
                "sales_rep_id": s.get("sales_rep_id"),
                "sales_rep_name": s.get("sales_rep_name"),
                "credit_limit": round(limit, 2),
                "credit_utilized": round(used, 2),
                "available_credit": round(limit - used, 2),
                "excess_amount": round(used - limit, 2),
            })
        return {"total_stores": len(rows), "stores": rows}

    @staticmethod
    def districts_summary(*, month_start, month_end):
        """District-wise breakdown: approved stores + monthly revenue
        + credit exposure. Revenue is Σ(order.total) over counted-status
        orders whose created_at falls in the [month_start, month_end]
        window, grouped by store_district. Credit exposure is
        Σ(credit_used) across approved stores in that district.

        Includes districts that have approved stores even if they had
        no orders this month (revenue = 0)."""
        from repository.order_repo import OrderRepository

        # Approved-store counts + credit_used per district.
        store_pipeline = [
            {"$match": {"status": "approved", "district": {"$nin": [None, ""]}}},
            {"$group": {
                "_id": "$district",
                "stores_count": {"$sum": 1},
                "credit_exposure": {"$sum": {"$ifNull": ["$credit_used", 0]}},
            }},
        ]
        store_rows = list(StoreRepository._coll().aggregate(store_pipeline))

        # Monthly revenue per district (counted-status orders only).
        counted = ("accepted", "packing", "out_for_delivery", "delivered")
        revenue_pipeline = [
            {"$match": {
                "status": {"$in": list(counted)},
                "created_at": {"$gte": month_start, "$lte": month_end},
                "store_district": {"$nin": [None, ""]},
            }},
            {"$group": {
                "_id": "$store_district",
                "revenue": {"$sum": "$total"},
            }},
        ]
        revenue_rows = {
            r["_id"]: float(r["revenue"] or 0)
            for r in OrderRepository._coll().aggregate(revenue_pipeline)
        }

        districts = []
        for r in store_rows:
            d = r["_id"]
            districts.append({
                "district_name": d,
                "stores_count": int(r["stores_count"] or 0),
                "total_monthly_revenue": round(revenue_rows.get(d, 0.0), 2),
                "total_credit_exposure": round(float(r["credit_exposure"] or 0), 2),
            })
        districts.sort(key=lambda x: x["total_monthly_revenue"], reverse=True)
        return {
            "total_districts": len(districts),
            "districts": districts,
        }

    @staticmethod
    def pending_credit_change_count():
        return StoreRepository._coll().count_documents(
            {"credit_change_status": CreditChangeStatus.PENDING.value}
        )

    @staticmethod
    def count_by_status(status):
        return StoreRepository._coll().count_documents({"status": status})

    @staticmethod
    def active_districts_count():
        """Distinct districts across approved stores."""
        vals = StoreRepository._coll().distinct(
            "district",
            {"status": "approved", "district": {"$nin": [None, ""]}},
        )
        return len([v for v in vals if v])

    @staticmethod
    def districts_for_rep(sales_rep_id):
        """Distinct set of districts across all stores currently assigned
        to this rep. Empty list if the rep has no stores yet."""
        vals = StoreRepository._coll().distinct(
            "district",
            {"sales_rep_id": sales_rep_id, "district": {"$nin": [None, ""]}},
        )
        return [v for v in vals if v]

    @staticmethod
    def list(sales_rep_id=None, status=None, credit_change_status=None,
             search=None, ids=None, statuses=None,
             skip=0, limit=50):
        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        if statuses:
            q["status"] = {"$in": list(statuses)}
        elif status:
            q["status"] = status
        if credit_change_status:
            q["credit_change_status"] = credit_change_status
        if ids:
            oids = [oid_or_none(i) for i in ids]
            oids = [o for o in oids if o is not None]
            if not oids:
                return [], 0
            q["_id"] = {"$in": oids}
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"location": {"$regex": search, "$options": "i"}},
                {"gst_number": {"$regex": search, "$options": "i"}},
                {"code": {"$regex": search, "$options": "i"}},
            ]
        cur = (
            StoreRepository._coll()
            .find(q)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        items = [_with_credit_state(to_public_doc(d)) for d in cur]
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
        district=None,
        status=StoreStatus.PENDING.value,
        credit_limit=0.0,
        credit_used=0.0,
        credit_period_days=30,
        is_free_cancellation=True,
        cancellation_charges=0.0,
        return_window_days=7,
    ):
        now = now_utc()
        doc = {
            "code": next_store_code(),
            "name": name,
            "location": location,
            "district": district,
            "contact": contact,
            "geo": geo,
            "email": email,
            "gst_number": gst_number,
            "notes": notes,
            "sales_rep_id": sales_rep_id,
            "sales_rep_name": sales_rep_name,
            "status": status,
            "credit_limit": float(credit_limit or 0.0),
            "credit_used": float(credit_used or 0.0),
            "pending_credit_limit": None,
            "credit_change_status": CreditChangeStatus.NONE.value,
            "reject_reason": None,
            "credit_period_days": int(credit_period_days if credit_period_days is not None else 30),
            "is_free_cancellation": bool(is_free_cancellation if is_free_cancellation is not None else True),
            "cancellation_charges": float(cancellation_charges or 0.0),
            "return_window_days": int(return_window_days if return_window_days is not None else 7),
            "created_at": now,
            "updated_at": now,
        }
        res = StoreRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return _with_credit_state(to_public_doc(doc))

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
                    "pending_credit_limit": None,
                    "credit_change_status": CreditChangeStatus.APPROVED.value,
                    "updated_at": now_utc(),
                }
            },
        )
        return StoreRepository.by_id(store_id)

    @staticmethod
    def propose_credit_limit(store_id, new_limit):
        """Office proposes a new credit_limit. Sits as pending until
        admin approves or rejects. Overwrites any prior pending proposal."""
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        StoreRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": {
                    "pending_credit_limit": float(new_limit),
                    "credit_change_status": CreditChangeStatus.PENDING.value,
                    "updated_at": now_utc(),
                }
            },
        )
        return StoreRepository.by_id(store_id)

    @staticmethod
    def approve_credit_limit(store_id):
        """Admin approves the pending credit_limit — apply it and clear
        the pending flag. Caller must check that a pending proposal
        actually exists."""
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        current = StoreRepository._coll().find_one({"_id": oid}, {"pending_credit_limit": 1})
        if not current or current.get("pending_credit_limit") is None:
            return None
        StoreRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": {
                    "credit_limit": float(current["pending_credit_limit"]),
                    "pending_credit_limit": None,
                    "credit_change_status": CreditChangeStatus.APPROVED.value,
                    "updated_at": now_utc(),
                }
            },
        )
        return StoreRepository.by_id(store_id)

    @staticmethod
    def reject_credit_limit(store_id):
        """Admin rejects the pending credit_limit — clear it, mark
        rejected. credit_limit stays untouched."""
        oid = oid_or_none(store_id)
        if oid is None:
            return None
        StoreRepository._coll().update_one(
            {"_id": oid},
            {
                "$set": {
                    "pending_credit_limit": None,
                    "credit_change_status": CreditChangeStatus.REJECTED.value,
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
