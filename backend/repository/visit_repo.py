from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc, today_iso_utc
from helpers.mongo import to_public_doc


class VisitRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.VISITS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = VisitRepository._coll()
        # Drop the old per-day unique constraint if it still exists —
        # multiple visits per day are now allowed. Best-effort; ignore if
        # the index isn't there.
        for name in list(coll.index_information().keys()):
            info = coll.index_information()[name]
            keys = info.get("key") or []
            # The old compound-unique index was on these three keys.
            if info.get("unique") and set(k[0] for k in keys) == {
                "sales_rep_id", "store_id", "visit_date",
            }:
                coll.drop_index(name)
                break
        coll.create_index([("sales_rep_id", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("store_id", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("mode", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("outcome", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("location_snapshot.district", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("marked_at", DESCENDING)])

    @staticmethod
    def insert(
        *,
        sales_rep_id,
        sales_rep_name,
        store_id,
        store_code=None,
        store_name=None,
        location_snapshot=None,
        mode,
        outcome,
        lat=None,
        lng=None,
        distance_meters=None,
        check_in=None,
        check_out=None,
        duration_minutes=None,
        order_id=None,
        order_value=None,
        no_order_reason=None,
        remarks=None,
    ):
        doc = {
            "sales_rep_id": sales_rep_id,
            "sales_rep_name": sales_rep_name,
            "store_id": store_id,
            "store_code": store_code,
            "store_name": store_name,
            "location_snapshot": location_snapshot,
            "visit_date": today_iso_utc(),
            "mode": mode,
            "outcome": outcome,
            "lat": (float(lat) if lat is not None else None),
            "lng": (float(lng) if lng is not None else None),
            "distance_meters": (float(distance_meters) if distance_meters is not None else None),
            "check_in": check_in,
            "check_out": check_out,
            "duration_minutes": duration_minutes,
            "order_id": order_id,
            "order_value": (float(order_value) if order_value is not None else None),
            "no_order_reason": no_order_reason,
            "remarks": remarks,
            "marked_at": now_utc(),
        }
        res = VisitRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def list(
        sales_rep_id=None,
        store_id=None,
        visit_date=None,
        mode=None,
        outcome=None,
        district=None,
        skip=0,
        limit=50,
    ):
        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        if store_id:
            q["store_id"] = store_id
        if visit_date:
            q["visit_date"] = visit_date
        if mode:
            q["mode"] = mode
        if outcome:
            q["outcome"] = outcome
        if district:
            q["location_snapshot.district"] = district
        cur = (
            VisitRepository._coll()
            .find(q)
            .sort("marked_at", DESCENDING)
            .skip(skip)
            .limit(limit)
        )
        items = [to_public_doc(d) for d in cur]
        total = VisitRepository._coll().count_documents(q)
        return items, total
