from datetime import datetime

from pydantic import BaseModel, Field


class DateRange(BaseModel):
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None


class RepAnalyticsTotals(BaseModel):
    revenue: float = 0.0
    orders: int = 0
    visits: int = 0
    in_store_visits: int = 0
    remote_visits: int = 0
    unique_stores_visited: int = 0
    repeat_visits: int = 0
    avg_visit_duration_minutes: float | None = None


class RepAnalyticsRatios(BaseModel):
    conversion_rate: float = 0.0
    avg_order_value: float = 0.0
    orders_per_visit: float = 0.0
    revenue_per_visit: float = 0.0
    revenue_per_order: float = 0.0
    avg_order_value_per_visit: float = 0.0
    in_store_pct: float = 0.0
    remote_pct: float = 0.0


class RepAnalytics(BaseModel):
    rep_id: str
    rep_name: str | None = None
    range: DateRange
    totals: RepAnalyticsTotals
    ratios: RepAnalyticsRatios


class MonthlyRepAnalyticsEntry(BaseModel):
    year: int
    month: int
    totals: RepAnalyticsTotals
    ratios: RepAnalyticsRatios


class MonthlyRepAnalytics(BaseModel):
    rep_id: str
    rep_name: str | None = None
    year: int
    months: list[MonthlyRepAnalyticsEntry]


class LeaderboardEntry(BaseModel):
    rep_id: str
    rep_name: str | None = None
    revenue: float = 0.0
    orders: int = 0
    visits: int = 0
    conversion_rate: float = 0.0
    avg_order_value: float = 0.0
    target: float | None = None
    target_achievement_pct: float | None = None


class Leaderboard(BaseModel):
    range: DateRange
    sort: str
    items: list[LeaderboardEntry]


class CategoryAchievement(BaseModel):
    category_id: str
    category_name: str | None = None
    target: float
    achieved: float
    percentage_achieved: float
    remaining: float


class TargetAchievement(BaseModel):
    rep_id: str
    rep_name: str | None = None
    year: int
    month: int
    monthly_target: float
    current_achievement: float
    percentage_achieved: float
    remaining_target: float
    category_wise: list[CategoryAchievement]
