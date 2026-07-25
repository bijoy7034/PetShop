from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from enums.notification import NotificationType


class Notification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    user_id: str
    type: NotificationType
    title: str
    body: str | None = None
    # Free-form context (achievement_id, progress_id, order_id, etc.)
    # so the frontend can deep-link without another server call.
    meta: dict = {}
    link: str | None = None
    is_read: bool = False
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[Notification]
    total: int
    unread_count: int
    page: int
    page_size: int


class UnreadCountResponse(BaseModel):
    unread_count: int


class AnnouncementCreate(BaseModel):
    """Admin broadcast body. `audience` scopes who receives it."""
    title: str = Field(min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=1000)
    audience: str = Field(
        default="all",
        pattern="^(all|admin|office|sales_rep|admin\\+office)$",
    )
    kind: str = Field(
        default="announcement",
        pattern="^(announcement|maintenance)$",
        description="'announcement' for general news; 'maintenance' for planned downtime.",
    )
    link: str | None = Field(default=None, max_length=500)


class AnnouncementResult(BaseModel):
    delivered_to: int
