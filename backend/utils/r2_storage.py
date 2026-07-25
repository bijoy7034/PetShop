import uuid
from pathlib import PurePosixPath

from config.config import settings
from config.logging.logger import logger


_ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class R2NotConfiguredError(RuntimeError):
    pass


_client = None


def _get_client():
    """Lazy-init the boto3 client. Raises R2NotConfiguredError if any of
    the required env vars are missing — the caller (route layer) turns
    that into a 503 so a missing config doesn't crash the whole app on
    import."""
    global _client
    if _client is not None:
        return _client

    missing = [
        name for name in (
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET",
            "R2_ENDPOINT_URL",
            "R2_PUBLIC_BASE_URL",
        )
        if not getattr(settings, name)
    ]
    if missing:
        raise R2NotConfiguredError(
            "R2 storage is not configured. Missing env vars: " + ", ".join(missing)
        )

    import boto3
    from botocore.config import Config

    _client = boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    logger.info(f"R2 client initialised for bucket={settings.R2_BUCKET}")
    return _client


def _ext_for(content_type, filename):
    """Pick the file extension we'll save under. Prefer the mime type
    (canonical); fall back to the filename's extension if the mime type
    is missing or unknown; else default to .bin."""
    ct = (content_type or "").lower().strip()
    if ct in _ALLOWED_CONTENT_TYPES:
        return _ALLOWED_CONTENT_TYPES[ct]
    if filename:
        suffix = PurePosixPath(filename).suffix.lower()
        if suffix in _ALLOWED_CONTENT_TYPES.values():
            return suffix
    return ".bin"


def is_allowed_image(content_type):
    return (content_type or "").lower() in _ALLOWED_CONTENT_TYPES


def upload_image(*, key_prefix, data, content_type, filename=None):
    """Write `data` (bytes) to R2 under `{key_prefix}/{uuid}{ext}` and
    return the public URL. `key_prefix` should be something like
    `products/PRD-0001` or `products/PRD-0001/variants/PRD-0001-V01`
    so images stay grouped per product/variant.
    """
    client = _get_client()
    ext = _ext_for(content_type, filename)
    key = f"{key_prefix.strip('/')}/{uuid.uuid4().hex}{ext}"

    client.put_object(
        Bucket=settings.R2_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type or "application/octet-stream",
        CacheControl="public, max-age=31536000, immutable",
    )
    public_url = f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"
    return {"key": key, "url": public_url}


def delete_image(key):
    """Best-effort delete. Silently swallows 'not found' — caller doesn't
    need to care whether the object was there."""
    client = _get_client()
    client.delete_object(Bucket=settings.R2_BUCKET, Key=key)


def upload_bytes(*, key, data, content_type="application/octet-stream",
                 cache_control=None):
    """Put raw bytes at an exact key (no UUID mangling). Used for DB
    archives, backups, generated reports — anything where the caller
    controls the storage path. Returns {key, url} where url is the
    public URL (may not be reachable if the bucket / prefix is private)."""
    client = _get_client()
    put_kwargs = {
        "Bucket": settings.R2_BUCKET,
        "Key": key,
        "Body": data,
        "ContentType": content_type,
    }
    if cache_control:
        put_kwargs["CacheControl"] = cache_control
    client.put_object(**put_kwargs)
    return {
        "key": key,
        "url": f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"
                if settings.R2_PUBLIC_BASE_URL else None,
    }


def get_signed_download_url(key, *, ttl_seconds=3600):
    """Time-limited presigned GET URL. Use for private objects (DB
    archives, sensitive reports) that shouldn't sit behind the public
    r2.dev URL. Works against R2's S3-compatible API."""
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.R2_BUCKET, "Key": key},
        ExpiresIn=int(ttl_seconds),
    )


def list_objects(prefix, *, limit=1000):
    """List keys under a prefix, ordered by last-modified descending —
    used by the admin dump listing so a user can see the archive
    history without leaving the app."""
    client = _get_client()
    resp = client.list_objects_v2(
        Bucket=settings.R2_BUCKET, Prefix=prefix, MaxKeys=limit,
    )
    contents = resp.get("Contents", []) or []
    contents.sort(key=lambda o: o.get("LastModified"), reverse=True)
    return [
        {
            "key": o["Key"],
            "size_bytes": int(o.get("Size") or 0),
            "last_modified": o.get("LastModified"),
        }
        for o in contents
    ]


def key_from_url(url):
    """Reverse the public URL back to an object key so DELETE can act on
    the stored URL directly. Returns None if the URL doesn't belong to
    our configured R2 prefix."""
    if not url or not settings.R2_PUBLIC_BASE_URL:
        return None
    base = settings.R2_PUBLIC_BASE_URL.rstrip("/") + "/"
    if not url.startswith(base):
        return None
    return url[len(base):]
