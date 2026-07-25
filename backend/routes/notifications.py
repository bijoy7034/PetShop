from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from enums.notification import NotificationType
from middleware.auth import require_admin, require_any_user
from repository.notification_repo import NotificationRepository
from schemas.notification import (
    AnnouncementCreate,
    AnnouncementResult,
    Notification,
    NotificationListResponse,
    UnreadCountResponse,
)
from services import notification_service
from services.audit_service import record


router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(False),
    since: datetime | None = Query(
        None,
        description="Only return notifications strictly newer than this ISO timestamp. "
                    "Use the last poll's max created_at to fetch only what's new.",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current=Depends(require_any_user),
):
    """Frontend polling endpoint. Suggested cadence: 30-60 s.
    Only returns rows belonging to the caller."""
    user = current["user"]
    items, total = NotificationRepository.list_for_user(
        user["_id"],
        unread_only=unread_only,
        since=since,
        page=page,
        page_size=page_size,
    )
    unread = NotificationRepository.unread_count(user["_id"])
    return NotificationListResponse(
        items=items, total=total, unread_count=unread,
        page=page, page_size=page_size,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(current=Depends(require_any_user)):
    """Cheap badge poll. One count query, no list fetch."""
    return UnreadCountResponse(
        unread_count=NotificationRepository.unread_count(current["user"]["_id"])
    )


@router.post("/{notification_id}/read", response_model=Notification)
async def mark_read(
    notification_id: str,
    request: Request,
    current=Depends(require_any_user),
):
    user = current["user"]
    doc = NotificationRepository.mark_read(notification_id, user["_id"])
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    record(
        AuditAction.NOTIFICATION_READ,
        ResourceType.NOTIFICATION,
        resource_id=notification_id,
        actor=user,
        request=request,
    )
    return doc


@router.post("/read-all")
async def mark_all_read(
    request: Request,
    current=Depends(require_any_user),
):
    user = current["user"]
    n = NotificationRepository.mark_all_read(user["_id"])
    record(
        AuditAction.NOTIFICATION_READ_ALL,
        ResourceType.NOTIFICATION,
        actor=user,
        after={"marked_read": n},
        request=request,
    )
    return {"marked_read": n}
