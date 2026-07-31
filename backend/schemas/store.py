from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from enums.store import CreditChangeStatus, StoreStatus


class GeoPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class ContactPerson(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None


class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=500)
    district: str | None = Field(default=None, max_length=120)
    contact: ContactPerson
    geo: GeoPoint
    email: EmailStr | None = None
    gst_number: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    sales_rep_id: str | None = None
    credit_limit: float | None = Field(default=None, ge=0)
    credit_period_days: int | None = Field(default=None, ge=0, le=365)
    is_free_cancellation: bool | None = None
    cancellation_charges: float | None = Field(default=None, ge=0)
    return_window_days: int | None = Field(default=None, ge=0, le=365)


class StoreUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=500)
    district: str | None = Field(default=None, max_length=120)
    contact: ContactPerson | None = None
    geo: GeoPoint | None = None
    email: EmailStr | None = None
    gst_number: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    credit_period_days: int | None = Field(default=None, ge=0, le=365)
    is_free_cancellation: bool | None = None
    cancellation_charges: float | None = Field(default=None, ge=0)
    return_window_days: int | None = Field(default=None, ge=0, le=365)


class StoreApprove(BaseModel):
    credit_limit: float = Field(ge=0)


class StoreReject(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class StoreAssign(BaseModel):
    sales_rep_id: str


class CreditLimitPropose(BaseModel):
    credit_limit: float = Field(ge=0)
    reason: str | None = Field(default=None, max_length=500)


class CreditLimitReject(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class Store(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    code: str | None = None
    name: str
    location: str
    district: str | None = None
    contact: ContactPerson
    geo: GeoPoint
    email: EmailStr | None = None
    gst_number: str | None = None
    notes: str | None = None
    sales_rep_id: str
    sales_rep_name: str | None = None
    status: StoreStatus = StoreStatus.PENDING
    credit_limit: float = 0.0
    credit_used: float = 0.0
    available_credit: float = 0.0
    is_over_credit_limit: bool = False
    pending_credit_limit: float | None = None
    credit_change_status: CreditChangeStatus = CreditChangeStatus.NONE
    reject_reason: str | None = None
    credit_period_days: int = 30
    is_free_cancellation: bool = True
    cancellation_charges: float = 0.0
    return_window_days: int = 7
    created_at: datetime
    updated_at: datetime


class StoreListResponse(BaseModel):
    items: list[Store]
    total: int
    page: int
    page_size: int


class RepCreditMetrics(BaseModel):
    total_credit_limit: float
    total_utilized_credit: float
    total_available_credit: float
    overdue_stores_count: int
    total_overdue_amount: float


class RepStoreSummary(BaseModel):
    """Assigned-store counts + rolled-up credit metrics for a rep."""
    total_assigned_stores: int
    approved_stores_count: int
    pending_stores_count: int
    rejected_stores_count: int
    credit_metrics: RepCreditMetrics


class CreditViolationsBreakdown(BaseModel):
    limit_exceeded_count: int
    period_overdue_count: int
    both_exceeded_count: int


class StoresCreditSummary(BaseModel):
    """Portfolio credit view across approved stores.
    `total_available_credit` may be negative when admin-approved
    over-credit orders have pushed credit_used past credit_limit."""
    total_customers: int
    total_credit_limit_sum: float
    total_outstanding_balance: float
    total_available_credit: float
    total_overdue_amount: float
    violating_stores_count: int
    violations_breakdown: CreditViolationsBreakdown
