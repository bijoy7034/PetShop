from pymongo import ASCENDING

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


def _to_public(doc):
    return to_public_doc(doc, drop=("password_hash",))


class UserRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.USERS_COLL]

    @staticmethod
    def ensure_indexes():
        UserRepository._coll().create_index([("email", ASCENDING)], unique=True)
        UserRepository._coll().create_index([("role", ASCENDING)])
        UserRepository._coll().create_index([("status", ASCENDING)])

    @staticmethod
    def by_id(user_id):
        oid = oid_or_none(user_id)
        if oid is None:
            return None
        return _to_public(UserRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def by_email(email):
        return _to_public(UserRepository._coll().find_one({"email": email.lower()}))

    @staticmethod
    def by_email_with_secret(email):
        doc = UserRepository._coll().find_one({"email": email.lower()})
        return to_public_doc(doc)

    @staticmethod
    def list(role=None, status=None, search=None, skip=0, limit=50):
        q = {}
        if role:
            q["role"] = role
        if status:
            q["status"] = status
        if search:
            q["$or"] = [
                {"email": {"$regex": search, "$options": "i"}},
                {"name": {"$regex": search, "$options": "i"}},
            ]
        cur = (
            UserRepository._coll()
            .find(q)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        items = [_to_public(d) for d in cur]
        total = UserRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def insert(email, name, role, password_hash, phone=None, must_change_password=False):
        now = now_utc()
        doc = {
            "email": email.lower(),
            "name": name,
            "role": role,
            "phone": phone,
            "status": "active",
            "password_hash": password_hash,
            "must_change_password": bool(must_change_password),
            "last_seen_at": None,
            "created_at": now,
            "updated_at": now,
        }
        res = UserRepository._coll().insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return _to_public(doc)

    @staticmethod
    def update(user_id, patch):
        oid = oid_or_none(user_id)
        if oid is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return UserRepository.by_id(user_id)
        patch["updated_at"] = now_utc()
        UserRepository._coll().update_one({"_id": oid}, {"$set": patch})
        return UserRepository.by_id(user_id)

    @staticmethod
    def delete(user_id):
        """Hard delete. Returns True on removal, False if no such user."""
        oid = oid_or_none(user_id)
        if oid is None:
            return False
        res = UserRepository._coll().delete_one({"_id": oid})
        return res.deleted_count == 1

    @staticmethod
    def set_last_seen(user_id):
        oid = oid_or_none(user_id)
        if oid is None:
            return
        UserRepository._coll().update_one({"_id": oid}, {"$set": {"last_seen_at": now_utc()}})
