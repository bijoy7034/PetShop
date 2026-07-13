from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


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
        return to_public_doc(CategoryRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def by_name(name):
        return to_public_doc(CategoryRepository._coll().find_one({"name": name}))

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
        items = [to_public_doc(d) for d in cur]
        total = CategoryRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def insert(name, description=None):
        now = now_utc()
        doc = {
            "name": name,
            "description": description,
            "created_at": now,
            "updated_at": now,
        }
        res = CategoryRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

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
