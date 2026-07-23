from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CategoryTarget(BaseModel):
    """Per-category target within a rep's monthly plan."""
    category_id: str
    category_name: str | None = None
    target: float = Field(ge=0)


class RepTargetCreate(BaseModel):
    rep_id: str
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    overall_target: float = Field(ge=0)
    category_targets: list[CategoryTarget] = []


class RepTargetUpdate(BaseModel):
    overall_target: float | None = Field(default=None, ge=0)
    category_targets: list[CategoryTarget] | None = None


class RepTarget(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    rep_id: str
    rep_name: str | None = None
    year: int
    month: int
    overall_target: float
    category_targets: list[CategoryTarget] = []
    created_by_id: str | None = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime


class RepTargetListResponse(BaseModel):
    items: list[RepTarget]
    total: int
    page: int
    page_size: int
