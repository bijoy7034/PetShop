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


class TopProduct(BaseModel):
    """One entry in a district's top-products list or a store's
    recommended-products list."""
    product_id: str
    product_code: str | None = None
    product_name: str
    variant_id: str
    variant_code: str | None = None
    variant_label: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    qty: int
    revenue: float
    orders: int
    last_ordered_at: datetime | None = None


class DistrictAnalyticsItem(BaseModel):
    district: str
    revenue: float
    orders: int
    unique_stores: int
    avg_order_value: float
    top_products: list[TopProduct]


class DistrictAnalytics(BaseModel):
    range: DateRange
    items: list[DistrictAnalyticsItem]


class RecommendedProductsResponse(BaseModel):
    store_id: str
    store_name: str | None = None
    source: str  # "store_history" | "global_fallback"
    items: list[TopProduct]


class DistrictTotals(BaseModel):
    revenue: float = 0.0
    orders: int = 0
    unique_stores: int = 0
    avg_order_value: float = 0.0


class CategoryRevenue(BaseModel):
    category_id: str
    category_name: str | None = None
    revenue: float
    orders: int


class DistrictStore(BaseModel):
    store_id: str
    store_code: str | None = None
    store_name: str | None = None
    revenue: float
    orders: int


class DistrictRep(BaseModel):
    rep_id: str
    rep_name: str | None = None
    revenue: float
    orders: int


class DistrictAnalyticsDetail(BaseModel):
    district: str
    range: DateRange
    totals: DistrictTotals
    top_products: list[TopProduct]
    by_category: list[CategoryRevenue]
    top_stores: list[DistrictStore]
    by_rep: list[DistrictRep]
