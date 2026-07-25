from enum import StrEnum


class AchievementPeriod(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AchievementMetric(StrEnum):
    # Orders that made it past acceptance — 'accepted' / 'packing' /
    # 'out_for_delivery' / 'delivered'. Excludes 'placed',
    # 'pending_admin_approval', 'delayed', and 'cancelled'. This is the
    # canonical "the rep landed a real order" metric.
    ORDERS_ACCEPTED = "orders_accepted"
    REVENUE_GENERATED = "revenue_generated"
    STORES_VISITED = "stores_visited"
    CONVERSION_RATE = "conversion_rate"


class AchievementProgressStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLAIMED = "claimed"       # Rep clicked claim, awaiting reward hand-out.
    REDEEMED = "redeemed"     # Admin/office confirmed the reward was given.
