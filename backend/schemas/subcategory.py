from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SubcategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category_id: str
    description: str | None = Field(default=None, max_length=1000)


class SubcategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category_id: str | None = None
    description: str | None = Field(default=None, max_length=1000)


class Subcategory(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    name: str
    category_id: str
    category_name: str | None = None
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class SubcategoryListResponse(BaseModel):
    items: list[Subcategory]
    total: int
    page: int
    page_size: int
