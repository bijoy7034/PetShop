from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GeoPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class RouteStop(BaseModel):
    store_id: str
    order_id: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    # Backend-populated on the response:
    sequence: int | None = None
    store_name: str | None = None
    order_code: str | None = None


class RouteOptimizeRequest(BaseModel):
    start: GeoPoint
    stops: list[RouteStop] = Field(min_length=1)
    driver_id: str | None = None
    driver_name: str | None = None


class OptimizedRoute(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    start: GeoPoint
    stops: list[RouteStop]
    total_distance_km: float
    total_duration_minutes: float
    polyline: str | None = None
    driver_id: str | None = None
    driver_name: str | None = None
    order_ids: list[str] = []
    created_at: datetime
    created_by_id: str | None = None
    created_by_name: str | None = None


class BulkDispatchRequest(BaseModel):
    order_ids: list[str] = Field(min_length=1)


class BulkDispatchSkip(BaseModel):
    order_id: str
    reason: str


class BulkDispatchResponse(BaseModel):
    dispatched_count: int
    skipped_count: int
    dispatched_ids: list[str]
    skipped: list[BulkDispatchSkip] = []
