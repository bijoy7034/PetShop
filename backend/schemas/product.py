from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VariantCreate(BaseModel):
    size: str | None = Field(default=None, max_length=60)
    weight: str | None = Field(default=None, max_length=60)
    color: str | None = Field(default=None, max_length=60)
    sku: str | None = Field(default=None, max_length=60)
    price: float = Field(ge=0)
    discount_price: float | None = Field(default=None, ge=0)
    stock: int = Field(default=0, ge=0)


class VariantUpdate(BaseModel):
    size: str | None = Field(default=None, max_length=60)
    weight: str | None = Field(default=None, max_length=60)
    color: str | None = Field(default=None, max_length=60)
    sku: str | None = Field(default=None, max_length=60)
    price: float | None = Field(default=None, ge=0)
    discount_price: float | None = Field(default=None, ge=0)


class Variant(BaseModel):
    id: str
    size: str | None = None
    weight: str | None = None
    color: str | None = None
    sku: str | None = None
    price: float
    discount_price: float | None = None
    stock: int


class StockAdjust(BaseModel):
    delta: int
    reason: str = Field(min_length=1, max_length=200)


class OptionSet(BaseModel):
    """Axes that define a product's variant matrix. Non-empty axes are
    combined into a Cartesian product server-side to auto-generate variants
    at product creation time. Empty or omitted axes are ignored."""
    size: list[str] | None = None
    weight: list[str] | None = None
    color: list[str] | None = None


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subcategory_id: str
    description: str | None = Field(default=None, max_length=4000)
    base_price: float = Field(ge=0)
    discount_price: float | None = Field(default=None, ge=0)
    option_sets: OptionSet | None = None


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    subcategory_id: str | None = None
    description: str | None = Field(default=None, max_length=4000)
    base_price: float | None = Field(default=None, ge=0)
    discount_price: float | None = Field(default=None, ge=0)


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    name: str
    subcategory_id: str
    subcategory_name: str | None = None
    category_id: str
    category_name: str | None = None
    description: str | None = None
    base_price: float
    discount_price: float | None = None
    variants: list[Variant] = []
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    items: list[Product]
    total: int
    page: int
    page_size: int


class BulkUploadRow(BaseModel):
    """Reported per-row outcome for the Excel bulk upload."""
    row: int
    action: str
    product_name: str | None = None
    error: str | None = None


class BulkUploadResponse(BaseModel):
    created: int
    updated: int
    failed: int
    rows: list[BulkUploadRow]
