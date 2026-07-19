from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from enums.store import StoreStatus


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
    # Only honoured when the caller is admin/office_staff (they can create
    # already-approved stores on behalf of a sales rep). Sales-rep callers
    # self-assign and these fields are ignored.
    sales_rep_id: str | None = None
    credit_limit: float | None = Field(default=None, ge=0)
    # Commercial terms — sent through when admin/office creates.
    # Sales rep creates with defaults; office can update via PATCH.
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
    reject_reason: str | None = None
    # Commercial terms snapshotted onto each order at placement so future
    # changes here don't rewrite historical orders.
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
