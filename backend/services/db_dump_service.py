"""Bi-weekly Mongo → R2 archive.

Iterates every collection, serialises each doc with bson.json_util so
ObjectIds/datetimes survive a round-trip, gzips the concatenated JSONL
per-collection into one `.tar.gz`, uploads to R2 under
`{R2_ARCHIVE_PREFIX}/YYYY/YYYY-MM-DDTHH-MM-SSZ.tar.gz`.

Runs in-process via `scripts/scheduler.py`, or on demand via the admin
route. Not idempotent — every call produces a fresh archive keyed by
timestamp.

Stays synchronous to match the rest of the codebase. For very large
DBs (tens of GB) this will hold the archive in memory; if you outgrow
that, switch to streaming into a temp file and swap `upload_bytes` for
a multipart-upload helper.
"""
import io
import tarfile
from datetime import datetime, timezone

from bson import json_util

from config.config import settings
from config.db import get_db
from config.logging.logger import logger
from utils import r2_storage


def _timestamp_key():
    """Object key of the form:
    db-archives/2026/2026-07-25T18-00-00Z.tar.gz — sortable both
    alphabetically and by the R2 dashboard's date filter."""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{settings.R2_ARCHIVE_PREFIX}/{now.year}/{stamp}.tar.gz"


def _collections_to_dump():
    db = get_db()
    excluded = set(settings.DB_DUMP_EXCLUDE_COLLS or [])
    names = db.list_collection_names()
    # Skip system collections (start with "system.") and anything the
    # operator marked exclude in env (default: sessions).
    return sorted(
        n for n in names
        if not n.startswith("system.") and n not in excluded
    )


def _dump_collection_to_jsonl(coll):
    """Serialise every doc in `coll` into a UTF-8 JSONL bytes blob. Uses
    bson.json_util so ObjectId/datetime/etc. round-trip via mongorestore
    or a matching restore script."""
    buf = io.BytesIO()
    doc_count = 0
    for doc in coll.find({}):
        line = json_util.dumps(doc, ensure_ascii=False) + "\n"
        buf.write(line.encode("utf-8"))
        doc_count += 1
    return buf.getvalue(), doc_count


def build_archive_bytes():
    """Iterate every collection and pack the JSONL files into one tar.gz.
    Returns (bytes, meta_dict). meta_dict carries per-collection counts
    and total size for logging / audit / route response."""
    db = get_db()
    per_coll_counts = {}
    total_docs = 0

    out = io.BytesIO()
    # gzip mode 9 = max compression. DB text compresses well; the
    # bandwidth win far outweighs the CPU cost every 14 days.
    with tarfile.open(fileobj=out, mode="w:gz", compresslevel=9) as tar:
        # Root manifest so restoring later is unambiguous.
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": db.name,
            "collections": [],
        }
        for name in _collections_to_dump():
            data, count = _dump_collection_to_jsonl(db[name])
            per_coll_counts[name] = count
            total_docs += count
            manifest["collections"].append({"name": name, "doc_count": count})
            info = tarfile.TarInfo(name=f"{name}.jsonl")
            info.size = len(data)
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            tar.addfile(info, io.BytesIO(data))
        # Add manifest last so it's easy to inspect first.
        manifest_bytes = json_util.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(manifest_bytes))

    archive_bytes = out.getvalue()
    return archive_bytes, {
        "database": db.name,
        "size_bytes": len(archive_bytes),
        "total_docs": total_docs,
        "per_collection": per_coll_counts,
    }


def run_dump():
    """Build the archive and upload it. Returns the R2 key + meta.
    Raises R2NotConfiguredError if R2 isn't set up — the caller (route
    or scheduler) decides whether to surface that or log-and-continue."""
    archive_bytes, meta = build_archive_bytes()
    key = _timestamp_key()
    result = r2_storage.upload_bytes(
        key=key, data=archive_bytes,
        content_type="application/gzip",
        cache_control="private, max-age=0, no-store",
    )
    logger.info(
        f"DB dump uploaded r2://{settings.R2_BUCKET}/{key} "
        f"— {meta['size_bytes']} bytes, {meta['total_docs']} docs"
    )
    return {"key": result["key"], **meta}


def list_archives(*, limit=50):
    return r2_storage.list_objects(settings.R2_ARCHIVE_PREFIX, limit=limit)


def get_download_url(key, *, ttl_seconds=3600):
    """Presigned GET URL for a specific archive key. TTL default 1 hour
    — long enough to click through, short enough that a leaked link
    goes stale fast."""
    return r2_storage.get_signed_download_url(key, ttl_seconds=ttl_seconds)
