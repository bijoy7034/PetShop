from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from enums.order import OrderStatus, PaymentStatus


class OrderLineCreate(BaseModel):
    product_id: str
    variant_id: str
    qty: int = Field(ge=1)


class OrderLine(BaseModel):
    product_id: str
    product_name: str
    variant_id: str
    variant_label: str | None = None
    qty: int
    unit_price: float
    line_total: float


class OrderCreate(BaseModel):
    store_id: str
    lines: list[OrderLineCreate] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=1000)


class OrderStatusEvent(BaseModel):
    status: OrderStatus
    at: datetime
    by_user_id: str | None = None
    by_user_name: str | None = None
    note: str | None = None


class PaymentEvent(BaseModel):
    amount: float
    method: str | None = None
    notes: str | None = None
    at: datetime
    by_user_id: str | None = None
    by_user_name: str | None = None


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    method: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=500)


class Order(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    store_id: str
    store_name: str | None = None
    sales_rep_id: str
    sales_rep_name: str | None = None
    status: OrderStatus
    lines: list[OrderLine]
    total: float
    notes: str | None = None
    history: list[OrderStatusEvent] = []
    cancel_reason: str | None = None
    # Payment ledger — orthogonal to the delivery status.
    payment_status: PaymentStatus = PaymentStatus.PENDING
    amount_paid: float = 0.0
    outstanding: float = 0.0
    payment_history: list[PaymentEvent] = []
    created_at: datetime
    updated_at: datetime


class OrderCancel(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class OrderListResponse(BaseModel):
    items: list[Order]
    total: int
    page: int
    page_size: int
