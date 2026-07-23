"""Live aggregation over orders + visits for rep-scoped analytics.

No new collection — every request runs one Mongo pipeline against the
existing orders and visits collections. Correct-by-construction (no
stale data risk), fine for small-to-medium volumes. Migrate to a
materialised rep_analytics collection later if reads slow down.
"""
from datetime import datetime, timedelta, timezone

from config.config import settings
from config.db import get_db


# Orders that count as "realised revenue". pending_admin_approval and
# cancelled are excluded — they never committed to inventory.
_COUNTED_ORDER_STATUSES = (
    "accepted", "packing", "out_for_delivery", "delivered",
)


def _orders_coll():
    return get_db()[settings.ORDERS_COLL]


def _visits_coll():
    return get_db()[settings.VISITS_COLL]


def _users_coll():
    return get_db()[settings.USERS_COLL]


def _rep_lookup(rep_id):
    doc = _users_coll().find_one({"_id": _oid(rep_id)}, {"name": 1})
    return (doc or {}).get("name")


def _oid(id_or_str):
    from helpers.mongo import oid_or_none
    return oid_or_none(id_or_str)


def _order_match(rep_id, dt_from, dt_to):
    q = {"status": {"$in": list(_COUNTED_ORDER_STATUSES)}}
    if rep_id:
        q["sales_rep_id"] = rep_id
    if dt_from or dt_to:
        q["created_at"] = {}
        if dt_from:
            q["created_at"]["$gte"] = dt_from
        if dt_to:
            q["created_at"]["$lte"] = dt_to
    return q


def _visit_match(rep_id, dt_from, dt_to):
    q = {}
    if rep_id:
        q["sales_rep_id"] = rep_id
    if dt_from or dt_to:
        q["marked_at"] = {}
        if dt_from:
            q["marked_at"]["$gte"] = dt_from
        if dt_to:
            q["marked_at"]["$lte"] = dt_to
    return q


def _ratio(numer, denom):
    return round(numer / denom, 4) if denom else 0.0


def _rep_totals(rep_id, dt_from, dt_to):
    """Aggregate raw counts + revenue for one rep in the given range."""
    orders_pipeline = [
        {"$match": _order_match(rep_id, dt_from, dt_to)},
        {"$group": {
            "_id": None,
            "revenue": {"$sum": "$total"},
            "orders": {"$sum": 1},
        }},
    ]
    orders_res = list(_orders_coll().aggregate(orders_pipeline))
    order_totals = orders_res[0] if orders_res else {"revenue": 0, "orders": 0}

    visits_pipeline = [
        {"$match": _visit_match(rep_id, dt_from, dt_to)},
        {"$group": {
            "_id": None,
            "visits": {"$sum": 1},
            "in_store": {
                "$sum": {"$cond": [{"$eq": ["$mode", "in_store"]}, 1, 0]}
            },
            "remote": {
                "$sum": {"$cond": [{"$eq": ["$mode", "remote"]}, 1, 0]}
            },
            "duration_sum": {"$sum": {"$ifNull": ["$duration_minutes", 0]}},
            "duration_count": {
                "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$duration_minutes", 0]}, 0]}, 1, 0]}
            },
            "store_ids": {"$addToSet": "$store_id"},
        }},
    ]
    visits_res = list(_visits_coll().aggregate(visits_pipeline))
    v = visits_res[0] if visits_res else {
        "visits": 0, "in_store": 0, "remote": 0,
        "duration_sum": 0, "duration_count": 0, "store_ids": [],
    }
    unique_stores = len(v["store_ids"])
    repeat_visits = max(0, v["visits"] - unique_stores)
    avg_dur = (
        round(v["duration_sum"] / v["duration_count"], 2)
        if v["duration_count"] else None
    )

    totals = {
        "revenue": float(order_totals["revenue"] or 0),
        "orders": int(order_totals["orders"] or 0),
        "visits": int(v["visits"]),
        "in_store_visits": int(v["in_store"]),
        "remote_visits": int(v["remote"]),
        "unique_stores_visited": unique_stores,
        "repeat_visits": repeat_visits,
        "avg_visit_duration_minutes": avg_dur,
    }
    return totals


def _ratios_from_totals(t):
    rev, orders, visits = t["revenue"], t["orders"], t["visits"]
    return {
        "conversion_rate": _ratio(orders, visits),
        "avg_order_value": _ratio(rev, orders),
        "orders_per_visit": _ratio(orders, visits),
        "revenue_per_visit": _ratio(rev, visits),
        "revenue_per_order": _ratio(rev, orders),
        "avg_order_value_per_visit": _ratio(rev, visits),
        "in_store_pct": round(
            (t["in_store_visits"] / visits * 100) if visits else 0.0, 2
        ),
        "remote_pct": round(
            (t["remote_visits"] / visits * 100) if visits else 0.0, 2
        ),
    }


def rep_analytics(rep_id, *, from_dt=None, to_dt=None, rep_name=None):
    totals = _rep_totals(rep_id, from_dt, to_dt)
    return {
        "rep_id": rep_id,
        "rep_name": rep_name or _rep_lookup(rep_id),
        "range": {"from": from_dt, "to": to_dt},
        "totals": totals,
        "ratios": _ratios_from_totals(totals),
    }


def _month_range(year, month):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end - timedelta(microseconds=1)


def monthly_trend(rep_id, year, *, rep_name=None):
    """Return one entry per month for the calendar year."""
    months = []
    for m in range(1, 13):
        start, end = _month_range(year, m)
        totals = _rep_totals(rep_id, start, end)
        months.append({
            "year": year,
            "month": m,
            "totals": totals,
            "ratios": _ratios_from_totals(totals),
        })
    return {
        "rep_id": rep_id,
        "rep_name": rep_name or _rep_lookup(rep_id),
        "year": year,
        "months": months,
    }


def leaderboard(*, from_dt=None, to_dt=None, sort="revenue", limit=100):
    """Cross-rep ranking. Two Mongo aggregations total, regardless of
    how many reps exist:
      1. group orders by sales_rep_id → revenue + order count
      2. group visits by sales_rep_id → visit count + in-store / remote
    Results merged in Python. Replaces the previous per-rep loop (N
    round-trips → 2)."""
    reps = list(_users_coll().find(
        {"role": "sales_rep", "status": "active"},
        {"name": 1},
    ))
    if not reps:
        return []

    orders_pipeline = [
        {"$match": _order_match(None, from_dt, to_dt)},
        {"$group": {
            "_id": "$sales_rep_id",
            "revenue": {"$sum": "$total"},
            "orders": {"$sum": 1},
        }},
    ]
    orders_by_rep = {
        r["_id"]: r for r in _orders_coll().aggregate(orders_pipeline)
    }

    visits_pipeline = [
        {"$match": _visit_match(None, from_dt, to_dt)},
        {"$group": {
            "_id": "$sales_rep_id",
            "visits": {"$sum": 1},
        }},
    ]
    visits_by_rep = {
        r["_id"]: r for r in _visits_coll().aggregate(visits_pipeline)
    }

    entries = []
    for r in reps:
        rid = str(r["_id"])
        o = orders_by_rep.get(rid, {})
        v = visits_by_rep.get(rid, {})
        revenue = float(o.get("revenue") or 0)
        orders = int(o.get("orders") or 0)
        visits = int(v.get("visits") or 0)
        entries.append({
            "rep_id": rid,
            "rep_name": r.get("name"),
            "revenue": revenue,
            "orders": orders,
            "visits": visits,
            "conversion_rate": _ratio(orders, visits),
            "avg_order_value": _ratio(revenue, orders),
            "target": None,
            "target_achievement_pct": None,
        })
    # target_achievement_pct is filled in by the route layer if the caller
    # asked for a month-based range, because we need the RepTarget doc.
    key = _sort_key(sort)
    entries.sort(key=key, reverse=True)
    return entries[:limit]


def _sort_key(sort):
    field_map = {
        "revenue": "revenue",
        "orders": "orders",
        "visits": "visits",
        "conversion_rate": "conversion_rate",
        "avg_order_value": "avg_order_value",
        "monthly_revenue": "revenue",
        "monthly_orders": "orders",
        "target_achievement_pct": "target_achievement_pct",
    }
    field = field_map.get(sort, "revenue")

    def _k(e):
        v = e.get(field)
        return -1 if v is None else v
    return _k


def target_achievement(rep_id, year, month, *, rep_name=None):
    """Combine current-month revenue with the matching RepTarget document.
    Category-wise achievement pulls from the order lines' category tags."""
    from repository.rep_target_repo import RepTargetRepository

    start, end = _month_range(year, month)
    totals = _rep_totals(rep_id, start, end)
    achieved = totals["revenue"]

    target_doc = RepTargetRepository.by_rep_month(rep_id, year, month)
    monthly_target = float((target_doc or {}).get("overall_target") or 0)
    pct = round(achieved / monthly_target * 100, 2) if monthly_target else 0.0
    remaining = max(0.0, monthly_target - achieved)

    # Category-wise: sum lines' effective revenue per category_id.
    cat_wise = []
    if target_doc and target_doc.get("category_targets"):
        cat_revenue = _revenue_by_category(rep_id, start, end)
        for ct in target_doc["category_targets"]:
            cid = ct["category_id"]
            achieved_cat = float(cat_revenue.get(cid, 0.0))
            target_cat = float(ct.get("target") or 0)
            pct_cat = (
                round(achieved_cat / target_cat * 100, 2) if target_cat else 0.0
            )
            cat_wise.append({
                "category_id": cid,
                "category_name": ct.get("category_name"),
                "target": target_cat,
                "achieved": achieved_cat,
                "percentage_achieved": pct_cat,
                "remaining": max(0.0, target_cat - achieved_cat),
            })

    return {
        "rep_id": rep_id,
        "rep_name": rep_name or _rep_lookup(rep_id),
        "year": year,
        "month": month,
        "monthly_target": monthly_target,
        "current_achievement": achieved,
        "percentage_achieved": pct,
        "remaining_target": remaining,
        "category_wise": cat_wise,
    }


def _revenue_by_category(rep_id, from_dt, to_dt):
    """Sum line_total per category_id across the rep's orders in range.
    Uses the denormalised category_id on each order line — no $lookup
    against the products collection. O(orders × lines) with an index
    hit on (sales_rep_id, created_at)."""
    pipeline = [
        {"$match": _order_match(rep_id, from_dt, to_dt)},
        {"$unwind": "$lines"},
        {"$match": {"lines.category_id": {"$ne": None}}},
        {"$group": {
            "_id": "$lines.category_id",
            "revenue": {"$sum": "$lines.line_total"},
        }},
    ]
    return {r["_id"]: r["revenue"] for r in _orders_coll().aggregate(pipeline) if r["_id"]}


def stores_visited_by_rep(rep_id, from_dt, to_dt):
    """Count of unique stores visited in the range — used by the
    STORES_VISITED achievement metric."""
    ids = _visits_coll().distinct(
        "store_id", _visit_match(rep_id, from_dt, to_dt)
    )
    return len(ids)
