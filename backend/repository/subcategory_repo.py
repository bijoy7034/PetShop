from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


class SubcategoryRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.SUBCATEGORIES_COLL]

    @staticmethod
    def ensure_indexes():
        coll = SubcategoryRepository._coll()
        coll.create_index([("category_id", ASCENDING), ("name", ASCENDING)], unique=True)
        coll.create_index([("category_id", ASCENDING)])

    @staticmethod
    def by_id(sub_id):
        oid = oid_or_none(sub_id)
        if oid is None:
            return None
        return to_public_doc(SubcategoryRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def by_name_in_category(name, category_id):
        return to_public_doc(
            SubcategoryRepository._coll().find_one(
                {"name": name, "category_id": category_id}
            )
        )

    @staticmethod
    def list(category_id=None, search=None, skip=0, limit=200):
        q = {}
        if category_id:
            q["category_id"] = category_id
        if search:
            q["name"] = {"$regex": search, "$options": "i"}
        cur = (
            SubcategoryRepository._coll()
            .find(q)
            .sort("name", ASCENDING)
            .skip(skip)
            .limit(limit)
        )
        items = [to_public_doc(d) for d in cur]
        total = SubcategoryRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def insert(name, category_id, category_name=None, description=None):
        now = now_utc()
        doc = {
            "name": name,
            "category_id": category_id,
            "category_name": category_name,
            "description": description,
            "created_at": now,
            "updated_at": now,
        }
        res = SubcategoryRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def update(sub_id, patch):
        oid = oid_or_none(sub_id)
        if oid is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return SubcategoryRepository.by_id(sub_id)
        patch["updated_at"] = now_utc()
        SubcategoryRepository._coll().update_one({"_id": oid}, {"$set": patch})
        return SubcategoryRepository.by_id(sub_id)

    @staticmethod
    def delete(sub_id):
        oid = oid_or_none(sub_id)
        if oid is None:
            return False
        res = SubcategoryRepository._coll().delete_one({"_id": oid})
        return res.deleted_count == 1

    @staticmethod
    def refresh_category_name(category_id, new_name):
        """Called when a category is renamed — keep the denormalised
        category_name in sync on every child subcategory."""
        SubcategoryRepository._coll().update_many(
            {"category_id": category_id},
            {"$set": {"category_name": new_name, "updated_at": now_utc()}},
        )
