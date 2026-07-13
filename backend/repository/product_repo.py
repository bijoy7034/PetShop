from bson import ObjectId
from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


def variant_label(v):
    parts = [p for p in (v.get("size"), v.get("weight"), v.get("color")) if p]
    if v.get("sku"):
        parts.append(f"SKU {v['sku']}")
    return " / ".join(parts) if parts else None


def _serialize_variants(variants):
    """Persist only variant-identity fields. Stock and reserved counts live
    in the inventory collection — they're intentionally absent here."""
    return [
        {
            "_id": ObjectId(),
            "size": v.get("size"),
            "weight": v.get("weight"),
            "color": v.get("color"),
            "sku": v.get("sku"),
            "price": float(v["price"]),
            "discount_price": (
                float(v["discount_price"]) if v.get("discount_price") is not None else None
            ),
        }
        for v in variants
    ]


def _hydrate_variants(variants):
    """Turn stored variant docs into API-shaped dicts. Live inventory is
    merged in by the route layer via _with_inventory to avoid an import
    cycle (inventory_repo -> product_repo)."""
    return [
        {
            "id": str(v["_id"]),
            "size": v.get("size"),
            "weight": v.get("weight"),
            "color": v.get("color"),
            "sku": v.get("sku"),
            "price": v["price"],
            "discount_price": v.get("discount_price"),
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
    ):
        now = now_utc()
        serialized = _serialize_variants(variants)
        doc = {
            "name": name,
            "subcategory_id": subcategory_id,
            "subcategory_name": subcategory_name,
            "category_id": category_id,
            "category_name": category_name,
            "description": description,
            "base_price": float(base_price),
            "discount_price": (
                float(discount_price) if discount_price is not None else None
            ),
            "variants": serialized,
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
        serialized = _serialize_variants([variant])[0]
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
    def update_variant(product_id, variant_id, patch):
        oid = oid_or_none(product_id)
        void = oid_or_none(variant_id)
        if oid is None or void is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return ProductRepository.by_id(product_id)
        set_doc = {f"variants.$.{k}": v for k, v in patch.items()}
        set_doc["updated_at"] = now_utc()
        ProductRepository._coll().update_one(
            {"_id": oid, "variants._id": void},
            {"$set": set_doc},
        )
        return ProductRepository.by_id(product_id)

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
        """Identity + price for a variant. Stock lives in inventory — the
        caller joins the two."""
        oid = oid_or_none(product_id)
        void = oid_or_none(variant_id)
        if oid is None or void is None:
            return None
        doc = ProductRepository._coll().find_one(
            {"_id": oid, "variants._id": void},
            {"name": 1, "variants.$": 1},
        )
        if not doc or not doc.get("variants"):
            return None
        v = doc["variants"][0]
        return {
            "product_id": str(doc["_id"]),
            "product_name": doc["name"],
            "variant_id": str(v["_id"]),
            "variant_label": variant_label(v),
            "price": v["price"],
            "discount_price": v.get("discount_price"),
        }
