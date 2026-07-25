from pydantic import BaseModel


class IdName(BaseModel):
    id: str
    name: str


class SubcategoryFilter(BaseModel):
    id: str
    name: str
    category_id: str
    category_name: str | None = None


class FiltersResponse(BaseModel):
    districts: list[str]
    sales_reps: list[IdName]
    categories: list[IdName]
    subcategories: list[SubcategoryFilter]
    order_statuses: list[str]
    payment_statuses: list[str]
    store_statuses: list[str]
    visit_modes: list[str]
    visit_outcomes: list[str]
    achievement_periods: list[str]
    achievement_metrics: list[str]
    achievement_progress_statuses: list[str]
    user_roles: list[str]
