from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


class DuplicateRepTargetError(Exception):
    """Raised when a target already exists for the same (rep, year, month).
    Enforced by a compound unique index so races between two concurrent
    creates can't both slip through."""


class RepTargetRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.REP_TARGETS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = RepTargetRepository._coll()
        coll.create_index(
            [("rep_id", ASCENDING), ("year", ASCENDING), ("month", ASCENDING)],
            unique=True,
        )
        coll.create_index([("year", ASCENDING), ("month", ASCENDING)])
        coll.create_index([("rep_id", ASCENDING), ("year", DESCENDING), ("month", DESCENDING)])

    @staticmethod
    def by_id(target_id):
        oid = oid_or_none(target_id)
        if oid is None:
            return None
        return to_public_doc(RepTargetRepository._coll().find_one({"_id": oid}))

    @staticmethod
    def by_rep_month(rep_id, year, month):
        return to_public_doc(
            RepTargetRepository._coll().find_one(
                {"rep_id": rep_id, "year": int(year), "month": int(month)}
            )
        )

    @staticmethod
    def list(rep_id=None, year=None, month=None, skip=0, limit=50):
        q = {}
        if rep_id:
            q["rep_id"] = rep_id
        if year is not None:
            q["year"] = int(year)
        if month is not None:
            q["month"] = int(month)
        cur = (
            RepTargetRepository._coll()
            .find(q)
            .sort([("year", DESCENDING), ("month", DESCENDING)])
            .skip(skip)
            .limit(limit)
        )
        items = [to_public_doc(d) for d in cur]
        total = RepTargetRepository._coll().count_documents(q)
        return items, total

    @staticmethod
    def insert(
        *,
        rep_id,
        rep_name,
        year,
        month,
        overall_target,
        category_targets,
        actor,
    ):
        now = now_utc()
        doc = {
            "rep_id": rep_id,
            "rep_name": rep_name,
            "year": int(year),
            "month": int(month),
            "overall_target": float(overall_target),
            "category_targets": category_targets,
            "created_by_id": (actor or {}).get("_id"),
            "created_by_name": (actor or {}).get("name"),
            "created_at": now,
            "updated_at": now,
        }
        try:
            res = RepTargetRepository._coll().insert_one(doc)
        except DuplicateKeyError as e:
            raise DuplicateRepTargetError() from e
        doc["_id"] = str(res.inserted_id)
        return to_public_doc(doc)

    @staticmethod
    def update(target_id, patch):
        oid = oid_or_none(target_id)
        if oid is None:
            return None
        patch = {k: v for k, v in patch.items() if v is not None}
        if not patch:
            return RepTargetRepository.by_id(target_id)
        patch["updated_at"] = now_utc()
        RepTargetRepository._coll().update_one({"_id": oid}, {"$set": patch})
        return RepTargetRepository.by_id(target_id)

    @staticmethod
    def delete(target_id):
        oid = oid_or_none(target_id)
        if oid is None:
            return False
        res = RepTargetRepository._coll().delete_one({"_id": oid})
        return res.deleted_count == 1
