from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from enums.visit import VisitMode, VisitOutcome


class VisitLocationSnapshot(BaseModel):
    """Snapshot of the store's location at the moment the visit was
    logged. Preserves historical district-wise reporting even if the
    store's address later changes."""
    location: str | None = None
    district: str | None = None
    geo_lat: float | None = None
    geo_lng: float | None = None


class VisitMark(BaseModel):
    store_id: str
    mode: VisitMode
    outcome: VisitOutcome
    # GPS required for in_store visits (geo-fence check). Not required for
    # remote visits.
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    # Optional check-in / check-out for in_store — duration_minutes is
    # computed from these on the server when both are present.
    check_in: datetime | None = None
    check_out: datetime | None = None
    # For order_placed outcomes, order_id is required and order_value is
    # server-populated from the order's total. For no_order outcomes,
    # no_order_reason is required.
    order_id: str | None = None
    no_order_reason: str | None = Field(default=None, max_length=500)
    remarks: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _validate(self):
        # Outcome ↔ payload consistency.
        if self.outcome == VisitOutcome.ORDER_PLACED:
            if not self.order_id:
                raise ValueError("order_id is required when outcome is 'order_placed'.")
            if self.no_order_reason:
                raise ValueError(
                    "no_order_reason must be empty when outcome is 'order_placed'."
                )
        else:
            if not (self.no_order_reason and self.no_order_reason.strip()):
                raise ValueError(
                    "no_order_reason is required when outcome is 'no_order'."
                )
            if self.order_id:
                raise ValueError(
                    "order_id must be empty when outcome is 'no_order'."
                )
        # Mode ↔ GPS consistency.
        if self.mode == VisitMode.IN_STORE:
            if self.lat is None or self.lng is None:
                raise ValueError(
                    "lat and lng are required for in-store visits (geo-fence check)."
                )
        # Check-in / check-out only make sense on in-store visits.
        if self.check_out and not self.check_in:
            raise ValueError("check_out requires check_in.")
        if self.check_in and self.check_out and self.check_out < self.check_in:
            raise ValueError("check_out cannot be before check_in.")
        return self


class Visit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    sales_rep_id: str
    sales_rep_name: str | None = None
    store_id: str
    store_code: str | None = None
    store_name: str | None = None
    location_snapshot: VisitLocationSnapshot | None = None
    visit_date: str
    mode: VisitMode
    outcome: VisitOutcome
    lat: float | None = None
    lng: float | None = None
    distance_meters: float | None = None
    check_in: datetime | None = None
    check_out: datetime | None = None
    duration_minutes: int | None = None
    order_id: str | None = None
    order_value: float | None = None
    no_order_reason: str | None = None
    remarks: str | None = None
    marked_at: datetime


class VisitListResponse(BaseModel):
    items: list[Visit]
    total: int
    page: int
    page_size: int
