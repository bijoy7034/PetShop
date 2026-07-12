from bson import ObjectId
from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


def _to_public(doc):
    if not doc:
        return None
    out = to_public_doc(doc)
    subs = out.get("subcategories") or []
    out["subcategories"] = [
        {"id": str(s["_id"]), "name": s["name"]} for s in subs
    ]
    return out


class CategoryRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.CATEGORIES_COLL]

    @staticmethod
    def ensure_indexes():
        CategoryRepository._coll().create_index(
            [("name", ASCENDING)], unique=True
        )

    @staticmethod
    def by_id(cat_id):
        oid = oid_or_none(cat_id)
        if oid is None:
            return None
        return _to_public(CategoryRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def by_name(name):
        return _to_public(CategoryRepository._coll().find_one({"name": name}))

    @staticmethod
    def list(search=None, skip=0, limit=100):
        q = {}
        if search:
            q["name"] = {"$regex": search, "$options": "i"}
        cur = (
            CategoryRepository._coll()
            .find(q)
            .sort("name", ASCENDING)
            .skip(skip)
            .limit(limit)
        )
        items = [_to_public(d) for d in cur]
        total = CategoryRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def insert(name, description=None):
        now = now_utc()
        doc = {
            "name": name,
            "description": description,
            "subcategories": [],
            "created_at": now,
            "updated_at": now,
        }
        res = CategoryRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return _to_public(doc)

    @staticmethod
    def update(cat_id, patch):
        oid = oid_or_none(cat_id)
        if oid is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return CategoryRepository.by_id(cat_id)
        patch["updated_at"] = now_utc()
        CategoryRepository._coll().update_one({"_id": oid}, {"$set": patch})
        return CategoryRepository.by_id(cat_id)

    @staticmethod
    def delete(cat_id):
        oid = oid_or_none(cat_id)
        if oid is None:
            return False
        res = CategoryRepository._coll().delete_one({"_id": oid})
        return res.deleted_count == 1

    @staticmethod
    def add_subcategory(cat_id, name):
        oid = oid_or_none(cat_id)
        if oid is None:
            return None
        sub_id = ObjectId()
        CategoryRepository._coll().update_one(
            {"_id": oid},
            {
                "$push": {"subcategories": {"_id": sub_id, "name": name}},
                "$set": {"updated_at": now_utc()},
            },
        )
        return CategoryRepository.by_id(cat_id)

    @staticmethod
    def update_subcategory(cat_id, sub_id, name):
        oid = oid_or_none(cat_id)
        soid = oid_or_none(sub_id)
        if oid is None or soid is None:
            return None
        CategoryRepository._coll().update_one(
            {"_id": oid, "subcategories._id": soid},
            {
                "$set": {
                    "subcategories.$.name": name,
                    "updated_at": now_utc(),
                }
            },
        )
        return CategoryRepository.by_id(cat_id)

    @staticmethod
    def remove_subcategory(cat_id, sub_id):
        oid = oid_or_none(cat_id)
        soid = oid_or_none(sub_id)
        if oid is None or soid is None:
            return None
        CategoryRepository._coll().update_one(
            {"_id": oid},
            {
                "$pull": {"subcategories": {"_id": soid}},
                "$set": {"updated_at": now_utc()},
            },
        )
        return CategoryRepository.by_id(cat_id)

    @staticmethod
    def has_subcategory(cat_id, sub_id):
        oid = oid_or_none(cat_id)
        soid = oid_or_none(sub_id)
        if oid is None or soid is None:
            return False
        return CategoryRepository._coll().count_documents(
            {"_id": oid, "subcategories._id": soid}, limit=1
        ) == 1
