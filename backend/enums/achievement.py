from enum import StrEnum


class AchievementPeriod(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AchievementMetric(StrEnum):
    ORDERS_COMPLETED = "orders_completed"
    REVENUE_GENERATED = "revenue_generated"
    STORES_VISITED = "stores_visited"
    CONVERSION_RATE = "conversion_rate"


class AchievementProgressStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLAIMED = "claimed"
