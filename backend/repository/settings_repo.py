"""Singleton settings collection — always one doc with _id='app_settings'.

Kept as a real collection (rather than an env-var bag) so the
developer console can hot-swap branding, currency, and theme colors
without a restart. Reads are cheap (find_one by _id).
"""
from config.config import settings
from config.db import get_db
from helpers.datetime import now_utc


_DOC_ID = "app_settings"

_DEFAULT = {
    "_id": _DOC_ID,
    "company_name": "Merxio",
    "address": None,
    "logo_url": None,
    "favicon_url": None,
    "gst_number": None,
    "phone": None,
    "email": None,
    "website": None,
    "support_email": None,
    "theme_colors": None,
    "currency": "INR",
    "currency_symbol": "₹",
    "extras": {},
    "updated_at": None,
    "updated_by_id": None,
    "updated_by_name": None,
}


class ClientSettingsRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.CLIENT_SETTINGS_COLL]

    @staticmethod
    def ensure_indexes():
        # Only one document — _id is the key.
        pass

    @staticmethod
    def get():
        doc = ClientSettingsRepository._coll().find_one({"_id": _DOC_ID})
        return doc or dict(_DEFAULT)

    @staticmethod
    def update(patch, *, actor):
        """Upsert the singleton with the fields in `patch`. Fields set
        to None in the patch are ignored (use `extras` to clear)."""
        clean = {k: v for k, v in patch.items() if v is not None}
        now = now_utc()
        clean["updated_at"] = now
        clean["updated_by_id"] = (actor or {}).get("_id")
        clean["updated_by_name"] = (actor or {}).get("name")
        ClientSettingsRepository._coll().update_one(
            {"_id": _DOC_ID},
            {"$set": clean, "$setOnInsert": {k: _DEFAULT[k] for k in _DEFAULT
                                              if k not in clean and k != "_id"}},
            upsert=True,
        )
        return ClientSettingsRepository.get()
