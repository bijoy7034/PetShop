from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc, today_iso_utc
from helpers.mongo import to_public_doc


class DuplicateVisitError(Exception):
    """Raised when a sales rep tries to log a second visit for the same
    store on the same UTC calendar day. Enforced by a compound unique index
    so races between two devices can't both slip through."""


class VisitRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.VISITS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = VisitRepository._coll()
        # One visit per (rep, store, UTC day) — the unique key. `visit_date`
        # is stored as a YYYY-MM-DD string so this survives clock drift and
        # timezone conversions on the client.
        coll.create_index(
            [("sales_rep_id", ASCENDING), ("store_id", ASCENDING), ("visit_date", ASCENDING)],
            unique=True,
        )
        coll.create_index([("sales_rep_id", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("store_id", ASCENDING), ("marked_at", DESCENDING)])
        coll.create_index([("marked_at", DESCENDING)])

    @staticmethod
    def insert(
        *,
        sales_rep_id,
        sales_rep_name,
        store_id,
        store_name,
        lat,
        lng,
        distance_meters,
        order_id=None,
        order_total=None,
        no_order_reason=None,
        remarks=None,
    ):
        doc = {
            "sales_rep_id": sales_rep_id,
            "sales_rep_name": sales_rep_name,
            "store_id": store_id,
            "store_name": store_name,
            "visit_date": today_iso_utc(),
            "lat": float(lat),
            "lng": float(lng),
            "distance_meters": float(distance_meters),
            "order_id": order_id,
            "order_total": (float(order_total) if order_total is not None else None),
            "no_order_reason": no_order_reason,
            "remarks": remarks,
            "marked_at": now_utc(),
        }
        try:
            res = VisitRepository._coll().insert_one(doc)
        except DuplicateKeyError as e:
            raise DuplicateVisitError() from e
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def list(sales_rep_id=None, store_id=None, visit_date=None, skip=0, limit=50):
        q = {}
        if sales_rep_id:
            q["sales_rep_id"] = sales_rep_id
        if store_id:
            q["store_id"] = store_id
        if visit_date:
            q["visit_date"] = visit_date
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
