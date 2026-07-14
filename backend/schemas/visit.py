from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VisitMark(BaseModel):
    store_id: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    # Exactly one of these two must be provided — the visit either resulted
    # in an order or it didn't, and the reason for a no-order visit must be
    # recorded for reporting.
    order_id: str | None = None
    no_order_reason: str | None = Field(default=None, max_length=500)
    remarks: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _xor_order_or_reason(self):
        has_order = bool(self.order_id)
        has_reason = bool(self.no_order_reason and self.no_order_reason.strip())
        if has_order == has_reason:
            raise ValueError(
                "Provide exactly one of order_id or no_order_reason "
                "(order placed vs. no-order visit)."
            )
        return self


class Visit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    sales_rep_id: str
    sales_rep_name: str | None = None
    store_id: str
    store_name: str | None = None
    visit_date: str
    lat: float
    lng: float
    distance_meters: float
    order_id: str | None = None
    order_total: float | None = None
    no_order_reason: str | None = None
    remarks: str | None = None
    marked_at: datetime


class VisitListResponse(BaseModel):
    items: list[Visit]
    total: int
    page: int
    page_size: int
