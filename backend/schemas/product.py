from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VariantCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    size: str | None = Field(default=None, max_length=60)
    weight: str | None = Field(default=None, max_length=60)
    color: str | None = Field(default=None, max_length=60)
    sku: str | None = Field(default=None, max_length=60)
    image: str | None = Field(default=None, max_length=1000)
    price: float = Field(ge=0)
    discount_price: float | None = Field(default=None, ge=0)
    initial_stock: int = Field(default=0, ge=0)
    reorder_level: int = Field(default=0, ge=0)


class VariantUpdate(BaseModel):
    """PATCH body for a variant. is_active is NOT here — activation is a
    lifecycle toggle with its own dedicated endpoint. If `price` or
    `discount_price` changes, the previous values are pushed to the
    variant's price_history[] before the new values are set, with the
    optional `reason` recorded on the history event."""
    name: str | None = Field(default=None, max_length=200)
    size: str | None = Field(default=None, max_length=60)
    weight: str | None = Field(default=None, max_length=60)
    color: str | None = Field(default=None, max_length=60)
    sku: str | None = Field(default=None, max_length=60)
    image: str | None = Field(default=None, max_length=1000)
    price: float | None = Field(default=None, ge=0)
    discount_price: float | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=200)


class PriceHistoryEvent(BaseModel):
    price: float
    discount_price: float | None = None
    variant_id: str
    changed_at: datetime
    changed_by_id: str | None = None
    changed_by_name: str | None = None
    reason: str | None = None


class Variant(BaseModel):
    id: str
    code: str | None = None
    is_active: bool = True
    name: str | None = None
    size: str | None = None
    weight: str | None = None
    color: str | None = None
    sku: str | None = None
    image: str | None = None
    price: float
    discount_price: float | None = None
    price_history: list[PriceHistoryEvent] = []
    # Live counts pulled from the inventory collection on read.
    quantity_on_hand: int = 0
    reserved_quantity: int = 0
    available: int = 0
    reorder_level: int = 0


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
    # Client-facing identifier (their own code, distinct from our
    # system-minted PRD-XXXX) and unit of sale (kg, pcs, box, ...).
    client_product_code: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=32)
    images: list[str] = []
    # Future-scalability fields — stored but not yet wired into behavior.
    tags: list[str] = []
    brand: str | None = Field(default=None, max_length=120)
    barcode: str | None = Field(default=None, max_length=64)
    cost_price: float | None = Field(default=None, ge=0)
    tax_rate: float | None = Field(default=None, ge=0, le=100)
    is_featured: bool = False
    is_refundable: bool = True
    is_returnable: bool = True


class ProductUpdate(BaseModel):
    """PATCH body. is_active is NOT here — activation is a lifecycle
    toggle with its own dedicated endpoint."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    subcategory_id: str | None = None
    description: str | None = Field(default=None, max_length=4000)
    base_price: float | None = Field(default=None, ge=0)
    discount_price: float | None = Field(default=None, ge=0)
    client_product_code: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=32)
    images: list[str] | None = None
    tags: list[str] | None = None
    brand: str | None = Field(default=None, max_length=120)
    barcode: str | None = Field(default=None, max_length=64)
    cost_price: float | None = Field(default=None, ge=0)
    tax_rate: float | None = Field(default=None, ge=0, le=100)
    is_featured: bool | None = None
    is_refundable: bool | None = None
    is_returnable: bool | None = None


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    code: str | None = None
    client_product_code: str | None = None
    is_active: bool = True
    name: str
    subcategory_id: str
    subcategory_name: str | None = None
    category_id: str
    category_name: str | None = None
    description: str | None = None
    unit: str | None = None
    images: list[str] = []
    base_price: float
    discount_price: float | None = None
    variants: list[Variant] = []
    # Future-scalability fields.
    tags: list[str] = []
    brand: str | None = None
    barcode: str | None = None
    cost_price: float | None = None
    tax_rate: float | None = None
    is_featured: bool = False
    is_refundable: bool = True
    is_returnable: bool = True
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
