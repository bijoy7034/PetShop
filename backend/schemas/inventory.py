from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Inventory(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    product_id: str
    product_name: str | None = None
    variant_id: str
    variant_label: str | None = None
    quantity_on_hand: int
    reserved_quantity: int
    available: int
    reorder_level: int
    created_at: datetime
    updated_at: datetime


class InventoryUpdate(BaseModel):
    reorder_level: int | None = Field(default=None, ge=0)


class StockAdjust(BaseModel):
    delta: int
    reason: str = Field(min_length=1, max_length=200)


class InventoryListResponse(BaseModel):
    items: list[Inventory]
    total: int
    page: int
    page_size: int
