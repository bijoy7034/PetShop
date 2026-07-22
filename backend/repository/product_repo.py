from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc
from repository.counter_repo import next_product_code, variant_code


def variant_label(v):
    if v.get("name"):
        return v["name"]
    parts = [p for p in (v.get("size"), v.get("weight"), v.get("color")) if p]
    if v.get("sku"):
        parts.append(f"SKU {v['sku']}")
    return " / ".join(parts) if parts else None


def _serialize_variants(variants, *, product_code, start_seq):
    out = []
    for i, v in enumerate(variants):
        out.append(
            {
                "_id": ObjectId(),
                "code": variant_code(product_code, start_seq + i),
                "seq": start_seq + i,
                "is_active": True,
                "name": v.get("name"),
                "size": v.get("size"),
                "weight": v.get("weight"),
                "color": v.get("color"),
                "sku": v.get("sku"),
                "image": v.get("image"),
                "price": float(v["price"]),
                "discount_price": (
                    float(v["discount_price"]) if v.get("discount_price") is not None else None
                ),
                "price_history": [],
            }
        )
    return out


def _hydrate_variants(variants):
    return [
        {
            "id": str(v["_id"]),
            "code": v.get("code"),
            "is_active": v.get("is_active", True),
            "name": v.get("name"),
            "size": v.get("size"),
            "weight": v.get("weight"),
            "color": v.get("color"),
            "sku": v.get("sku"),
            "image": v.get("image"),
            "price": v["price"],
            "discount_price": v.get("discount_price"),
            "price_history": v.get("price_history") or [],
            "quantity_on_hand": 0,
            "reserved_quantity": 0,
            "available": 0,
            "reorder_level": 0,
        }
        for v in variants
    ]


def _to_public(doc):
    if not doc:
        return None
    out = to_public_doc(doc)
    out["variants"] = _hydrate_variants(out.get("variants") or [])
    return out


class ProductRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.PRODUCTS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = ProductRepository._coll()
        coll.create_index([("name", ASCENDING)])
        coll.create_index([("category_id", ASCENDING)])
        coll.create_index([("subcategory_id", ASCENDING)])
        coll.create_index([("variants.sku", ASCENDING)])

    @staticmethod
    def by_id(product_id):
        oid = oid_or_none(product_id)
        if oid is None:
            return None
        return _to_public(ProductRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def by_name(name):
        return _to_public(ProductRepository._coll().find_one({"name": name}))

    @staticmethod
    def list(category_id=None, subcategory_id=None, search=None, skip=0, limit=50):
        q = {}
        if category_id:
            q["category_id"] = category_id
        if subcategory_id:
            q["subcategory_id"] = subcategory_id
        if search:
            q["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"variants.sku": {"$regex": search, "$options": "i"}},
            ]
        cur = (
            ProductRepository._coll()
            .find(q)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        items = [_to_public(d) for d in cur]
        total = ProductRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def insert(
        *,
        name,
        subcategory_id,
        subcategory_name,
        category_id,
        category_name,
        description,
        base_price,
        discount_price,
        variants,
        client_product_code=None,
        unit=None,
        images=None,
        tags=None,
        brand=None,
        barcode=None,
        cost_price=None,
        tax_rate=None,
        is_featured=False,
        is_refundable=True,
        is_returnable=True,
    ):
        now = now_utc()
        product_code = next_product_code()
        serialized = _serialize_variants(variants, product_code=product_code, start_seq=1)
        doc = {
            "code": product_code,
            "client_product_code": client_product_code,
            "next_variant_seq": len(serialized) + 1,
            "is_active": True,
            "name": name,
            "subcategory_id": subcategory_id,
            "subcategory_name": subcategory_name,
            "category_id": category_id,
            "category_name": category_name,
            "description": description,
            "unit": unit,
            "images": list(images or []),
            "base_price": float(base_price),
            "discount_price": (
                float(discount_price) if discount_price is not None else None
            ),
            "variants": serialized,
            "tags": list(tags or []),
            "brand": brand,
            "barcode": barcode,
            "cost_price": (float(cost_price) if cost_price is not None else None),
            "tax_rate": (float(tax_rate) if tax_rate is not None else None),
            "is_featured": bool(is_featured),
            "is_refundable": bool(is_refundable),
            "is_returnable": bool(is_returnable),
            "created_at": now,
            "updated_at": now,
        }
        res = ProductRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        # Return the persisted (identity-only) variants plus the input dicts
        # so the route layer can seed inventory with initial_stock/reorder_level.
        seed = []
        for stored, incoming in zip(serialized, variants):
            seed.append(
                {
                    "variant_id": str(stored["_id"]),
                    "variant_label": variant_label(stored),
                    "initial_stock": int(incoming.get("initial_stock") or 0),
                    "reorder_level": int(incoming.get("reorder_level") or 0),
                }
            )
        public = _to_public(doc)
        public["_inventory_seed"] = seed
        return public

    @staticmethod
    def refresh_taxonomy_names(*, subcategory_id=None, category_id=None,
                                subcategory_name=None, category_name=None):
        patch = {"updated_at": now_utc()}
        if subcategory_name is not None:
            patch["subcategory_name"] = subcategory_name
        if category_name is not None:
            patch["category_name"] = category_name
        if len(patch) == 1:
            return
        q = {}
        if subcategory_id:
            q["subcategory_id"] = subcategory_id
        if category_id:
            q["category_id"] = category_id
        if not q:
            return
        ProductRepository._coll().update_many(q, {"$set": patch})

    @staticmethod
    def update(product_id, patch):
        oid = oid_or_none(product_id)
        if oid is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return ProductRepository.by_id(product_id)
        patch["updated_at"] = now_utc()
        ProductRepository._coll().update_one({"_id": oid}, {"$set": patch})
        return ProductRepository.by_id(product_id)

    @staticmethod
    def delete(product_id):
        oid = oid_or_none(product_id)
        if oid is None:
            return False
        res = ProductRepository._coll().delete_one({"_id": oid})
        return res.deleted_count == 1

    @staticmethod
    def add_variant(product_id, variant):
        oid = oid_or_none(product_id)
        if oid is None:
            return None, None
        # Atomically bump the product's per-variant counter so concurrent
        # adds can't collide on the same sequence number, then push the
        # variant with the code built from that seq. Two round trips, but
        # correct under contention.
        parent = ProductRepository._coll().find_one_and_update(
            {"_id": oid},
            {"$inc": {"next_variant_seq": 1}},
            projection={"code": 1, "next_variant_seq": 1},
            return_document=ReturnDocument.AFTER,
        )
        if not parent:
            return None, None
        seq = int(parent["next_variant_seq"]) - 1
        serialized = _serialize_variants(
            [variant], product_code=parent.get("code"), start_seq=seq
        )[0]
        ProductRepository._coll().update_one(
            {"_id": oid},
            {
                "$push": {"variants": serialized},
                "$set": {"updated_at": now_utc()},
            },
        )
        seed = {
            "variant_id": str(serialized["_id"]),
            "variant_label": variant_label(serialized),
            "initial_stock": int(variant.get("initial_stock") or 0),
            "reorder_level": int(variant.get("reorder_level") or 0),
        }
        return ProductRepository.by_id(product_id), seed

    @staticmethod
    def update_variant(product_id, variant_id, patch, *, actor=None, reason=None):
        """PATCH a variant. If `price` or `discount_price` change, the
        previous (price, discount_price) is pushed to the variant's
        price_history[] BEFORE the new values are set, so history and
        current state are updated atomically in one Mongo call."""
        oid = oid_or_none(product_id)
        void = oid_or_none(variant_id)
        if oid is None or void is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return ProductRepository.by_id(product_id)

        # Detect a price change to decide whether we also need to push a
        # history entry. Cheap projection read first.
        history_event = None
        if "price" in patch or "discount_price" in patch:
            current = ProductRepository._coll().find_one(
                {"_id": oid, "variants._id": void},
                {"variants.$": 1},
            )
            if current and current.get("variants"):
                v = current["variants"][0]
                old_price = float(v.get("price") or 0)
                old_dp = v.get("discount_price")
                new_price = float(patch.get("price", old_price))
                new_dp = patch["discount_price"] if "discount_price" in patch else old_dp
                if new_price != old_price or new_dp != old_dp:
                    history_event = {
                        "price": old_price,
                        "discount_price": old_dp,
                        "variant_id": str(void),
                        "changed_at": now_utc(),
                        "changed_by_id": (actor or {}).get("_id"),
                        "changed_by_name": (actor or {}).get("name"),
                        "reason": reason,
                    }

        set_doc = {f"variants.$.{k}": v for k, v in patch.items()}
        set_doc["updated_at"] = now_utc()
        update = {"$set": set_doc}
        if history_event:
            update["$push"] = {"variants.$.price_history": history_event}
        ProductRepository._coll().update_one(
            {"_id": oid, "variants._id": void},
            update,
        )
        return ProductRepository.by_id(product_id)

    @staticmethod
    def toggle_active(product_id):
        """Atomically flip product.is_active."""
        oid = oid_or_none(product_id)
        if oid is None:
            return None
        # Read → compute → write. Use find_one_and_update with an
        # aggregation-pipeline update so the flip is atomic. Requires
        # MongoDB 4.2+ (Atlas is fine).
        doc = ProductRepository._coll().find_one_and_update(
            {"_id": oid},
            [{"$set": {
                "is_active": {"$not": ["$is_active"]},
                "updated_at": now_utc(),
            }}],
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return None
        return _to_public(doc)

    @staticmethod
    def toggle_variant_active(product_id, variant_id):
        """Atomically flip a variant's is_active flag."""
        oid = oid_or_none(product_id)
        void = oid_or_none(variant_id)
        if oid is None or void is None:
            return None
        doc = ProductRepository._coll().find_one_and_update(
            {"_id": oid, "variants._id": void},
            [{"$set": {
                "variants": {
                    "$map": {
                        "input": "$variants",
                        "as": "v",
                        "in": {
                            "$cond": [
                                {"$eq": ["$$v._id", void]},
                                {"$mergeObjects": [
                                    "$$v",
                                    {"is_active": {"$not": [{"$ifNull": ["$$v.is_active", True]}]}},
                                ]},
                                "$$v",
                            ]
                        },
                    }
                },
                "updated_at": now_utc(),
            }}],
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return None
        return _to_public(doc)

    @staticmethod
    def remove_variant(product_id, variant_id):
        oid = oid_or_none(product_id)
        void = oid_or_none(variant_id)
        if oid is None or void is None:
            return None
        ProductRepository._coll().update_one(
            {"_id": oid},
            {
                "$pull": {"variants": {"_id": void}},
                "$set": {"updated_at": now_utc()},
            },
        )
        return ProductRepository.by_id(product_id)

    @staticmethod
    def get_variant(product_id, variant_id):
        """Identity + price for a variant. Also returns is_active flags on
        both the product and the variant so callers (order placement)
        can reject inactive items. Stock lives in inventory — the caller
        joins the two."""
        oid = oid_or_none(product_id)
        void = oid_or_none(variant_id)
        if oid is None or void is None:
            return None
        doc = ProductRepository._coll().find_one(
            {"_id": oid, "variants._id": void},
            {"name": 1, "code": 1, "is_active": 1, "variants.$": 1},
        )
        if not doc or not doc.get("variants"):
            return None
        v = doc["variants"][0]
        return {
            "product_id": str(doc["_id"]),
            "product_code": doc.get("code"),
            "product_name": doc["name"],
            "product_active": doc.get("is_active", True),
            "variant_id": str(v["_id"]),
            "variant_code": v.get("code"),
            "variant_label": variant_label(v),
            "variant_active": v.get("is_active", True),
            "price": v["price"],
            "discount_price": v.get("discount_price"),
        }
