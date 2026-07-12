from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SubcategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SubcategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class Subcategory(BaseModel):
    id: str
    name: str


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class Category(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    name: str
    description: str | None = None
    subcategories: list[Subcategory] = []
    created_at: datetime
    updated_at: datetime


class CategoryListResponse(BaseModel):
    items: list[Category]
    total: int
    page: int
    page_size: int
