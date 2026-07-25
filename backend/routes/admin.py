from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_admin
from repository.audit_repo import AuditRepository
from schemas.audit import AuditLogListResponse
from services import db_dump_service
from services.audit_service import record
from utils.r2_storage import R2NotConfiguredError


router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/db-dump/run-now")
async def db_dump_run_now(
    request: Request,
    current=Depends(require_admin),
):
    """Kick off a fresh archive immediately. Sync — blocks until the
    upload finishes (usually a few seconds; scales with total doc count).
    Returns the R2 key + a one-hour presigned download URL so the caller
    can grab the file without visiting the Cloudflare dashboard."""
    try:
        result = db_dump_service.run_dump()
    except R2NotConfiguredError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))

    signed = db_dump_service.get_download_url(result["key"], ttl_seconds=3600)
    record(
        AuditAction.USER_UPDATE,  # No dedicated action yet — reusing.
        ResourceType.USER,
        actor=current["user"],
        after={
            "action": "db_dump.manual",
            "key": result["key"],
            "size_bytes": result["size_bytes"],
            "total_docs": result["total_docs"],
        },
        request=request,
    )
    return {**result, "download_url": signed, "download_url_ttl_seconds": 3600}


@router.get("/db-dump/archives")
async def db_dump_list_archives(
    limit: int = Query(50, ge=1, le=500),
    _=Depends(require_admin),
):
    """List archives currently in R2 (most recent first). Cheap
    `list_objects_v2` call, no downloads."""
    try:
        items = db_dump_service.list_archives(limit=limit)
    except R2NotConfiguredError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    return {"items": items, "total": len(items)}


@router.post("/db-dump/download-url")
async def db_dump_download_url(
    key: str = Query(..., description="R2 object key returned by /archives"),
    ttl_seconds: int = Query(3600, ge=60, le=86400),
    _=Depends(require_admin),
):
    """Mint a fresh presigned URL for one archive. Use when the one
    returned by run-now has expired."""
    try:
        url = db_dump_service.get_download_url(key, ttl_seconds=ttl_seconds)
    except R2NotConfiguredError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    return {"key": key, "download_url": url, "ttl_seconds": ttl_seconds}


# --------- Audit log read routes ---------

@router.get("/audit-logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    action: str | None = Query(None, description="Exact match, e.g. 'order.accept'"),
    resource_type: str | None = Query(None),
    resource_id: str | None = Query(None),
    actor_id: str | None = Query(None),
    actor_email: str | None = Query(None, description="Case-insensitive substring."),
    ip: str | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    search: str | None = Query(
        None,
        description="Free-text search across action / resource_type / actor.",
    ),
    sort_by: str = Query(
        "at",
        description="at | action | resource_type | actor_email",
    ),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(require_admin),
):
    """Every write across the app leaves an audit row. Admin-only read
    with filters + sort + pagination. Returns the full `before`/`after`
    diff so a UI can render a state-change timeline. Indexed on
    (at desc), (actor_id), (resource_type, resource_id), (action)."""
    items, total = AuditRepository.list(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        actor_email=actor_email,
        ip=ip,
        from_dt=from_,
        to_dt=to,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        skip=(page - 1) * page_size,
        limit=page_size,
    )
    return AuditLogListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/audit-logs/actions")
async def audit_log_actions(_=Depends(require_admin)):
    """Distinct action names — used to populate the frontend filter."""
    return {"actions": AuditRepository.distinct_actions()}


@router.get("/audit-logs/resource-types")
async def audit_log_resource_types(_=Depends(require_admin)):
    return {"resource_types": AuditRepository.distinct_resource_types()}
