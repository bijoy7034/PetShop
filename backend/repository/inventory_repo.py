from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


def _to_public(doc):
    out = to_public_doc(doc)
    if out is None:
        return None
    on_hand = out.get("quantity_on_hand", 0)
    reserved = out.get("reserved_quantity", 0)
    out["available"] = max(0, on_hand - reserved)
    return out


class InventoryRepository:
    """One document per variant. Never write to `variants[i].stock` — this is
    the source of truth for on-hand and reserved quantities. Every mutation
    is atomic and guards against underflow so concurrent orders can never
    double-book the same units."""

    @staticmethod
    def _coll():
        return get_db()[settings.INVENTORY_COLL]

    @staticmethod
    def ensure_indexes():
        coll = InventoryRepository._coll()
        coll.create_index([("variant_id", ASCENDING)], unique=True)
        coll.create_index([("product_id", ASCENDING)])

    @staticmethod
    def by_variant_id(variant_id):
        return _to_public(
            InventoryRepository._coll().find_one({"variant_id": variant_id})
        )

    @staticmethod
    def by_variant_ids(variant_ids):
        """Bulk fetch used to hydrate product responses."""
        if not variant_ids:
            return {}
        cur = InventoryRepository._coll().find({"variant_id": {"$in": list(variant_ids)}})
        return {d["variant_id"]: _to_public(d) for d in cur}

    @staticmethod
    def by_id(inv_id):
        oid = oid_or_none(inv_id)
        if oid is None:
            return None
        return _to_public(InventoryRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def create(
        *,
        product_id,
        variant_id,
        variant_label=None,
        product_name=None,
        quantity_on_hand=0,
        reorder_level=0,
    ):
        existing = InventoryRepository.by_variant_id(variant_id)
        if existing:
            return existing
        now = now_utc()
        opening = int(quantity_on_hand or 0)
        doc = {
            "product_id": product_id,
            "variant_id": variant_id,
            "variant_label": variant_label,
            "product_name": product_name,
            "quantity_on_hand": opening,
            "reserved_quantity": 0,
            "reorder_level": int(reorder_level or 0),
            "stock_history": (
                [
                    {
                        "previous_stock": 0,
                        "new_stock": opening,
                        "delta": opening,
                        "reason": "Opening balance at variant create",
                        "changed_by_id": None,
                        "changed_by_name": None,
                        "changed_at": now,
                    }
                ]
                if opening > 0
                else []
            ),
            "last_stock_updated_at": now if opening > 0 else None,
            "last_stock_updated_by_id": None,
            "last_stock_updated_by_name": None,
            "updated_at": now,
            "created_at": now,
        }
        res = InventoryRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return _to_public(doc)

    @staticmethod
    def refresh_labels(variant_id, *, variant_label=None, product_name=None):
        """Keep denormalised labels in sync when variant options or product
        name change."""
        patch = {"updated_at": now_utc()}
        if variant_label is not None:
            patch["variant_label"] = variant_label
        if product_name is not None:
            patch["product_name"] = product_name
        if len(patch) == 1:
            return
        InventoryRepository._coll().update_one(
            {"variant_id": variant_id}, {"$set": patch}
        )

    @staticmethod
    def refresh_product_name(product_id, product_name):
        InventoryRepository._coll().update_many(
            {"product_id": product_id},
            {"$set": {"product_name": product_name, "updated_at": now_utc()}},
        )

    @staticmethod
    def set_reorder_level(inv_id, reorder_level):
        oid = oid_or_none(inv_id)
        if oid is None:
            return None
        InventoryRepository._coll().update_one(
            {"_id": oid},
            {"$set": {"reorder_level": int(reorder_level), "updated_at": now_utc()}},
        )
        return InventoryRepository.by_id(inv_id)

    @staticmethod
    def adjust_on_hand(variant_id, delta, *, reason=None, actor=None):
        """Atomic bump of quantity_on_hand. Refuses to drop below the current
        reserved quantity (that would strand orders that already reserved
        against these units).

        When a `reason` is supplied we also append a stock_history event
        and stamp last_stock_updated_at/by, giving a complete audit trail
        of on-hand changes. Callers that don't pass a reason (internal
        wiring like create-with-opening-balance) skip the log."""
        current = InventoryRepository._coll().find_one(
            {"variant_id": variant_id},
            {"quantity_on_hand": 1, "reserved_quantity": 1},
        )
        if not current:
            return None
        prev = int(current.get("quantity_on_hand") or 0)
        new_stock = prev + int(delta)
        # Must still cover the reserved.
        if new_stock < int(current.get("reserved_quantity") or 0):
            return None
        now = now_utc()
        set_doc = {"quantity_on_hand": new_stock, "updated_at": now}
        push_doc = None
        if reason is not None:
            event = {
                "previous_stock": prev,
                "new_stock": new_stock,
                "delta": int(delta),
                "reason": reason,
                "changed_by_id": (actor or {}).get("_id"),
                "changed_by_name": (actor or {}).get("name"),
                "changed_at": now,
            }
            push_doc = {"stock_history": event}
            set_doc["last_stock_updated_at"] = now
            set_doc["last_stock_updated_by_id"] = (actor or {}).get("_id")
            set_doc["last_stock_updated_by_name"] = (actor or {}).get("name")
        update = {"$set": set_doc}
        if push_doc:
            update["$push"] = push_doc
        # Guard against a race between our read and write.
        res = InventoryRepository._coll().update_one(
            {"variant_id": variant_id, "quantity_on_hand": prev},
            update,
        )
        if res.matched_count == 0:
            return None
        return InventoryRepository.by_variant_id(variant_id)

    @staticmethod
    def reserve(variant_id, qty):
        """Reserve `qty` units. Refuses if it would push reserved above
        on-hand (i.e. would oversell). Returns the fresh doc or None."""
        qty = int(qty)
        if qty <= 0:
            return None
        res = InventoryRepository._coll().update_one(
            {
                "variant_id": variant_id,
                "$expr": {
                    "$gte": [
                        {"$subtract": ["$quantity_on_hand", "$reserved_quantity"]},
                        qty,
                    ]
                },
            },
            {
                "$inc": {"reserved_quantity": qty},
                "$set": {"updated_at": now_utc()},
            },
        )
        if res.matched_count == 0:
            return None
        return InventoryRepository.by_variant_id(variant_id)

    @staticmethod
    def release(variant_id, qty):
        """Release a prior reservation (order cancelled). Refuses if the
        release would push reserved below zero."""
        qty = int(qty)
        if qty <= 0:
            return None
        res = InventoryRepository._coll().update_one(
            {"variant_id": variant_id, "reserved_quantity": {"$gte": qty}},
            {
                "$inc": {"reserved_quantity": -qty},
                "$set": {"updated_at": now_utc()},
            },
        )
        if res.matched_count == 0:
            return None
        return InventoryRepository.by_variant_id(variant_id)

    @staticmethod
    def commit(variant_id, qty, *, order_code=None, actor=None):
        """Order accepted — turn a reservation into a real consumption:
        decrement BOTH reserved_quantity and quantity_on_hand by qty.
        Logs a stock_history event tagged with the order that consumed
        the stock."""
        qty = int(qty)
        if qty <= 0:
            return None
        current = InventoryRepository._coll().find_one(
            {"variant_id": variant_id},
            {"quantity_on_hand": 1, "reserved_quantity": 1},
        )
        if not current:
            return None
        prev = int(current.get("quantity_on_hand") or 0)
        prev_reserved = int(current.get("reserved_quantity") or 0)
        if prev < qty or prev_reserved < qty:
            return None
        now = now_utc()
        reason = (
            f"Order committed: {order_code}" if order_code else "Order committed"
        )
        event = {
            "previous_stock": prev,
            "new_stock": prev - qty,
            "delta": -qty,
            "reason": reason,
            "changed_by_id": (actor or {}).get("_id"),
            "changed_by_name": (actor or {}).get("name"),
            "changed_at": now,
        }
        res = InventoryRepository._coll().update_one(
            {
                "variant_id": variant_id,
                "quantity_on_hand": prev,
                "reserved_quantity": {"$gte": qty},
            },
            {
                "$inc": {"reserved_quantity": -qty, "quantity_on_hand": -qty},
                "$set": {
                    "updated_at": now,
                    "last_stock_updated_at": now,
                    "last_stock_updated_by_id": (actor or {}).get("_id"),
                    "last_stock_updated_by_name": (actor or {}).get("name"),
                },
                "$push": {"stock_history": event},
            },
        )
        if res.matched_count == 0:
            return None
        return InventoryRepository.by_variant_id(variant_id)

    @staticmethod
    def delete_by_variant(variant_id):
        InventoryRepository._coll().delete_one({"variant_id": variant_id})

    @staticmethod
    def delete_by_product(product_id):
        InventoryRepository._coll().delete_many({"product_id": product_id})

    @staticmethod
    def list(product_id=None, low_stock=None, skip=0, limit=50):
        q = {}
        if product_id:
            q["product_id"] = product_id
        if low_stock:
            q["$expr"] = {
                "$and": [
                    {"$gt": ["$reorder_level", 0]},
                    {
                        "$lt": [
                            {"$subtract": ["$quantity_on_hand", "$reserved_quantity"]},
                            "$reorder_level",
                        ]
                    },
                ]
            }
        cur = (
            InventoryRepository._coll()
            .find(q)
            .sort("updated_at", -1)
            .skip(skip)
            .limit(limit)
        )
        items = [_to_public(d) for d in cur]
        total = InventoryRepository._coll().count_documents(q)
        return items, total
