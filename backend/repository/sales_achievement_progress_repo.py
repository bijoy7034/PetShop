from pymongo import ASCENDING, DESCENDING

from config.config import settings
from config.db import get_db
from enums.achievement import AchievementProgressStatus
from helpers.datetime import now_utc
from helpers.mongo import oid_or_none, to_public_doc


class SalesAchievementProgressRepository:
    """Per-rep progress record for an achievement. Kept lightweight: the
    current_value is recomputed on read from underlying orders/visits;
    only status and claim timestamps are durable."""

    @staticmethod
    def _coll():
        return get_db()[settings.SALES_ACHIEVEMENT_PROGRESS_COLL]

    @staticmethod
    def ensure_indexes():
        coll = SalesAchievementProgressRepository._coll()
        # One progress row per (achievement, rep). Compound unique so
        # concurrent auto-creates can't produce duplicates.
        coll.create_index(
            [("achievement_id", ASCENDING), ("sales_rep_id", ASCENDING)],
            unique=True,
        )
        coll.create_index([("sales_rep_id", ASCENDING), ("status", ASCENDING)])
        coll.create_index([("achievement_id", ASCENDING)])
        coll.create_index([("updated_at", DESCENDING)])

    @staticmethod
    def by_id(progress_id):
        oid = oid_or_none(progress_id)
        if oid is None:
            return None
        return to_public_doc(
            SalesAchievementProgressRepository._coll().find_one({"_id": oid})
        )

    @staticmethod
    def by_achievement_and_rep(achievement_id, rep_id):
        return to_public_doc(
            SalesAchievementProgressRepository._coll().find_one(
                {"achievement_id": achievement_id, "sales_rep_id": rep_id}
            )
        )

    @staticmethod
    def list_by_rep(rep_id):
        cur = (
            SalesAchievementProgressRepository._coll()
            .find({"sales_rep_id": rep_id})
            .sort("updated_at", DESCENDING)
        )
        return [to_public_doc(d) for d in cur]

    @staticmethod
    def ensure_row(*, achievement, sales_rep):
        """Idempotent create. If a row for (achievement, rep) already
        exists, returns it untouched. Otherwise inserts a fresh
        in_progress row with current_value=0."""
        existing = SalesAchievementProgressRepository.by_achievement_and_rep(
            achievement["_id"], sales_rep["_id"]
        )
        if existing:
            return existing
        now = now_utc()
        doc = {
            "achievement_id": achievement["_id"],
            "achievement_title": achievement.get("title"),
            "achievement_metric": (achievement.get("target") or {}).get("metric"),
            "achievement_period": achievement.get("period"),
            "reward": achievement.get("reward"),
            "sales_rep_id": sales_rep["_id"],
            "sales_rep_name": sales_rep.get("name"),
            "current_value": 0.0,
            "target_value": float((achievement.get("target") or {}).get("value") or 0),
            "status": AchievementProgressStatus.IN_PROGRESS.value,
            "completed_at": None,
            "claimed_at": None,
            "created_at": now,
            "updated_at": now,
        }
        # Race-safe: the compound unique index makes concurrent inserts
        # error out; catch and re-read.
        from pymongo.errors import DuplicateKeyError
        try:
            res = SalesAchievementProgressRepository._coll().insert_one(doc)
            doc["_id"] = str(res.inserted_id)
            return to_public_doc(doc)
        except DuplicateKeyError:
            return SalesAchievementProgressRepository.by_achievement_and_rep(
                achievement["_id"], sales_rep["_id"]
            )

    @staticmethod
    def set_value(progress_id, *, current_value, status, completed_at=None):
        oid = oid_or_none(progress_id)
        if oid is None:
            return None
        patch = {
            "current_value": float(current_value),
            "status": status,
            "updated_at": now_utc(),
        }
        if completed_at is not None:
            patch["completed_at"] = completed_at
        SalesAchievementProgressRepository._coll().update_one(
            {"_id": oid}, {"$set": patch}
        )
        return SalesAchievementProgressRepository.by_id(progress_id)

    @staticmethod
    def mark_claimed(progress_id):
        oid = oid_or_none(progress_id)
        if oid is None:
            return None
        now = now_utc()
        # Only allow claim if status is 'completed' (or in_progress with
        # current >= target). Route layer enforces the invariant; here
        # we just stamp.
        SalesAchievementProgressRepository._coll().update_one(
            {"_id": oid},
            {"$set": {
                "status": AchievementProgressStatus.CLAIMED.value,
                "claimed_at": now,
                "updated_at": now,
            }},
        )
        return SalesAchievementProgressRepository.by_id(progress_id)

    @staticmethod
    def delete_by_achievement(achievement_id):
        SalesAchievementProgressRepository._coll().delete_many(
            {"achievement_id": achievement_id}
        )
