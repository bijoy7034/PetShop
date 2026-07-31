from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from enums.order import OrderStatus, PaymentStatus


class OrderLineCreate(BaseModel):
    product_id: str
    variant_id: str
    qty: int = Field(ge=1)


class OrderLineAdjust(BaseModel):
    product_id: str
    variant_id: str
    qty: int = Field(ge=0)


class OrderLine(BaseModel):
    product_id: str
    product_code: str | None = None
    product_name: str
    # Denormalised at placement so category-wise analytics doesn't need
    # to $lookup the products collection.
    category_id: str | None = None
    category_name: str | None = None
    subcategory_id: str | None = None
    subcategory_name: str | None = None
    variant_id: str
    variant_code: str | None = None
    variant_label: str | None = None
    # qty_ordered is set at placement and never changes. qty_accepted is
    # null while status='placed', and set at acceptance — equals
    # qty_ordered unless office reduced it in the accept body.
    qty_ordered: int
    qty_accepted: int | None = None
    unit_price: float
    discount_price: float | None = None
    line_total: float


class OrderCreate(BaseModel):
    store_id: str
    lines: list[OrderLineCreate] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=1000)
    expected_delivery_date: datetime | None = None
    # Override the store's default credit_period_days for THIS order
    # only. Snapshotted onto the order at placement so future store
    # edits don't rewrite the due date. Defaults to the store's setting
    # (30 days if the store has none).
    credit_period_days: int | None = Field(default=None, ge=0, le=365)


class OrderStatusEvent(BaseModel):
    # Includes every OrderStatus PLUS 'edited' for lines-edit events.
    # History is an event log, not just a status timeline — a plain str
    # keeps it open for future event kinds without touching this schema.
    status: str
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


class DeliveryAddressSnapshot(BaseModel):
    """Snapshot of the store's contact/address at the time the order was
    placed. Preserves historical delivery data if the store is later
    edited or reassigned."""
    name: str | None = None
    location: str | None = None
    district: str | None = None
    gst_number: str | None = None
    geo_lat: float | None = None
    geo_lng: float | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None


class Order(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    code: str | None = None
    status: OrderStatus
    last_status_updated_at: datetime | None = None

    # --- Denormalised store snapshot (survives future store edits) ---
    store_id: str
    store_code: str | None = None
    store_name: str | None = None
    store_district: str | None = None
    delivery_address_snapshot: DeliveryAddressSnapshot | None = None

    # --- Sales rep snapshot ---
    sales_rep_id: str
    sales_rep_name: str | None = None

    # --- Lines & totals ---
    lines: list[OrderLine]
    total: float
    notes: str | None = None

    # --- Timelines ---
    history: list[OrderStatusEvent] = []
    expected_delivery_date: datetime | None = None
    delivered_at: datetime | None = None

    # --- Approval / cancellation / delay reasons ---
    cancel_reason: str | None = None
    rejection_reason: str | None = None
    delay_reason: str | None = None

    # --- Who accepted the order ---
    accepted_by_id: str | None = None
    accepted_by_name: str | None = None

    # --- Payment ledger (orthogonal to delivery status) ---
    payment_status: PaymentStatus = PaymentStatus.PENDING
    amount_paid: float = 0.0
    outstanding: float = 0.0
    payment_history: list[PaymentEvent] = []

    # --- Credit terms snapshot (from store at order create) ---
    credit_period_days: int = 30
    payment_due_date: datetime | None = None

    # --- Over-credit approval flag ---
    over_credit_approved: bool = False
    over_credit_amount: float = 0.0
    over_credit_approved_at: datetime | None = None
    over_credit_approved_by_id: str | None = None
    over_credit_approved_by_name: str | None = None

    # --- Backorder split ---
    # When a rep places an order with a mix of in-stock and out-of-stock
    # lines the server splits into two orders and cross-links them via
    # sibling_order_id. If the whole order is out-of-stock, one order is
    # created (no sibling) with status=waiting_for_stock.
    sibling_order_id: str | None = None
    # Timestamp of the last successful auto-promotion from
    # waiting_for_stock → ready_to_submit. Lets the UI show "ready since
    # <date>".
    ready_to_submit_at: datetime | None = None

    # --- Policy snapshots (from store at order create) ---
    is_free_cancellation: bool = True
    cancellation_charges: float = 0.0
    return_window_days: int = 7

    created_at: datetime
    updated_at: datetime


class OrderCancel(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class OrderReject(BaseModel):
    """Admin rejection of a pending_admin_approval order. Reason mandatory."""
    reason: str = Field(min_length=1, max_length=500)


class OrderDelay(BaseModel):
    """Mark an active order as delayed. Reason mandatory."""
    reason: str = Field(min_length=1, max_length=500)


class OrderAccept(BaseModel):
    """Body for POST /orders/{id}/accept. All fields are optional — an
    empty body accepts every line at its qty_ordered.

    If `lines` is present, each entry must refer to a (product_id,
    variant_id) that already exists on the order. `qty` becomes the
    qty_accepted and must be between 0 (fully drop this line) and
    qty_ordered (never larger). Any line omitted from the body is
    accepted at its original qty_ordered. Surplus reservations
    (qty_ordered − qty_accepted) are released back to inventory."""
    lines: list[OrderLineAdjust] | None = None
    note: str | None = Field(default=None, max_length=500)


class OrderListResponse(BaseModel):
    items: list[Order]
    total: int
    page: int
    page_size: int


class OrderStats(BaseModel):
    """Dashboard aggregation: totals + status breakdowns.

    counts_by_status keys cover every value of OrderStatus (waiting_for_stock,
    ready_to_submit, pending_admin_approval, placed, accepted, packing,
    out_for_delivery, delivered, delayed, cancelled) — zero-filled when the
    status has no orders. Same guarantee for counts_by_payment_status
    (pending, partially_paid, paid).
    """
    total_orders: int
    total_volume: float
    counts_by_status: dict[str, int]
    counts_by_payment_status: dict[str, int]


class PlaceOrderResponse(BaseModel):
    """Wrapper returned by POST /orders. When the request has a mix of
    in-stock and out-of-stock lines the server splits it into two
    orders: `primary_order` (in-stock, entering the fulfilment pipeline)
    and `waiting_order` (out-of-stock, status=waiting_for_stock).

    When every line is in-stock, `waiting_order` is None. When every
    line is out-of-stock, `primary_order` is the waiting order and
    `waiting_order` is None (there was nothing to split off)."""
    primary_order: Order
    waiting_order: Order | None = None
    split: bool = False
