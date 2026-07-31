"""Delivery-route optimization + persistence.

Uses greedy nearest-neighbor on haversine distance — an O(n²) TSP
heuristic that's plenty for the ≤ 50 stops a real delivery run has.
If OSRM_URL is configured, we'd defer to it for both the sequence
and the polyline; that hook is intentionally left as a TODO — nothing
in production depends on it yet.

Persisted routes carry the driver, generated stops with sequence
numbers, and the total distance/duration so ops can audit runs after
the fact.
"""
import math
from datetime import datetime, timezone

from bson import ObjectId

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc


# Rough average urban delivery speed used to estimate duration when
# OSRM isn't available. ~25 km/h accounts for traffic + drop-off time.
_AVG_SPEED_KMH = 25.0


def _haversine_km(a, b):
    """Great-circle distance in km between two {lat,lng} dicts."""
    lat1, lon1 = math.radians(a["lat"]), math.radians(a["lng"])
    lat2, lon2 = math.radians(b["lat"]), math.radians(b["lng"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def optimize(*, start, stops):
    """Greedy nearest-neighbor from `start`. Returns (ordered_stops,
    total_km). Each stop dict is returned unchanged except for an
    added `sequence` (1-indexed)."""
    remaining = list(stops)
    ordered = []
    current = {"lat": start["lat"], "lng": start["lng"]}
    total_km = 0.0
    seq = 1
    while remaining:
        best_i, best_d = 0, _haversine_km(current, remaining[0])
        for i in range(1, len(remaining)):
            d = _haversine_km(current, remaining[i])
            if d < best_d:
                best_i, best_d = i, d
        nxt = remaining.pop(best_i)
        total_km += best_d
        nxt = {**nxt, "sequence": seq}
        ordered.append(nxt)
        current = {"lat": nxt["lat"], "lng": nxt["lng"]}
        seq += 1
    return ordered, round(total_km, 2)


def _hydrate_stop_metadata(stops):
    """Look up store_name + order_code for each stop so the response
    is self-contained (frontend doesn't need to join)."""
    from repository.order_repo import OrderRepository
    from repository.store_repo import StoreRepository
    out = []
    for s in stops:
        store = StoreRepository.by_id(s["store_id"])
        order = OrderRepository.by_id(s["order_id"])
        out.append({
            **s,
            "store_name": (store or {}).get("name"),
            "order_code": (order or {}).get("code"),
        })
    return out


def plan_route(*, start, stops, driver_id=None, driver_name=None,
               label=None, actor=None):
    """Simpler variant of create_route for office-staff planning: takes
    raw address stops (lat/lng required, everything else optional) and
    stores the result with a human label. Same optimization heuristic
    as create_route — greedy nearest-neighbor haversine — but skips the
    store/order metadata hydration."""
    ordered, total_km = optimize(start=start, stops=stops)
    duration_min = round(total_km / _AVG_SPEED_KMH * 60, 1)
    doc = {
        "_id": ObjectId(),
        "label": label,
        "start": start,
        "stops": ordered,
        "total_distance_km": total_km,
        "total_duration_minutes": duration_min,
        "polyline": None,
        "driver_id": driver_id,
        "driver_name": driver_name,
        "order_ids": [s["order_id"] for s in ordered if s.get("order_id")],
        "created_at": now_utc(),
        "created_by_id": (actor or {}).get("_id"),
        "created_by_name": (actor or {}).get("name"),
    }
    get_db()[settings.DELIVERY_ROUTES_COLL].insert_one(doc)
    doc["_id"] = str(doc["_id"])
    return doc


def list_routes(*, driver_id=None, page=1, page_size=50):
    from helpers.mongo import to_public_doc
    q = {}
    if driver_id:
        q["driver_id"] = driver_id
    skip = (page - 1) * page_size
    coll = get_db()[settings.DELIVERY_ROUTES_COLL]
    cur = (
        coll.find(q)
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
    )
    items = [to_public_doc(d) for d in cur]
    total = coll.count_documents(q)
    return items, total


def by_id(route_id):
    from helpers.mongo import oid_or_none, to_public_doc
    oid = oid_or_none(route_id)
    if oid is None:
        return None
    doc = get_db()[settings.DELIVERY_ROUTES_COLL].find_one({"_id": oid})
    return to_public_doc(doc)


def create_route(*, start, stops, driver_id=None, driver_name=None, actor=None):
    """Optimize + hydrate + persist. Returns the stored route doc."""
    ordered, total_km = optimize(start=start, stops=stops)
    ordered = _hydrate_stop_metadata(ordered)
    duration_min = round(total_km / _AVG_SPEED_KMH * 60, 1)

    doc = {
        "_id": ObjectId(),
        "start": start,
        "stops": ordered,
        "total_distance_km": total_km,
        "total_duration_minutes": duration_min,
        "polyline": None,  # OSRM hook — not implemented
        "driver_id": driver_id,
        "driver_name": driver_name,
        "order_ids": [s["order_id"] for s in ordered],
        "created_at": now_utc(),
        "created_by_id": (actor or {}).get("_id"),
        "created_by_name": (actor or {}).get("name"),
    }
    get_db()[settings.DELIVERY_ROUTES_COLL].insert_one(doc)
    doc["_id"] = str(doc["_id"])
    return doc
