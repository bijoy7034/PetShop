from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttendanceMark(BaseModel):
    store_id: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    notes: str | None = Field(default=None, max_length=1000)


class Attendance(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    user_id: str
    user_name: str | None = None
    store_id: str
    store_name: str | None = None
    lat: float
    lng: float
    distance_meters: float
    notes: str | None = None
    marked_at: datetime


class AttendanceListResponse(BaseModel):
    items: list[Attendance]
    total: int
    page: int
    page_size: int
