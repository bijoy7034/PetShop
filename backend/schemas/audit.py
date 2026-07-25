from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditLog(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    action: str
    resource_type: str
    resource_id: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    actor_role: str | None = None
    before: dict | None = None
    after: dict | None = None
    at: datetime
    request_id: str | None = None
    ip: str | None = None


class AuditLogListResponse(BaseModel):
    items: list[AuditLog]
    total: int
    page: int
    page_size: int
