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


class StoreUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=500)
    contact: ContactPerson | None = None
    geo: GeoPoint | None = None
    email: EmailStr | None = None
    gst_number: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)


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
    created_at: datetime
    updated_at: datetime


class StoreListResponse(BaseModel):
    items: list[Store]
    total: int
    page: int
    page_size: int
