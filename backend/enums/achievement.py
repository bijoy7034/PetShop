from enum import StrEnum


class AchievementPeriod(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AchievementMetric(StrEnum):
    # Every order the rep placed that wasn't cancelled (includes
    # placed / pending_admin_approval / accepted / packing / out_for_delivery
    # / delivered / delayed). Rewards raw activity.
    ORDERS_PLACED = "orders_placed"
    # Only orders that made it past acceptance (accepted / packing /
    # out_for_delivery / delivered). Rewards fulfilled business.
    ORDERS_COMPLETED = "orders_completed"
    REVENUE_GENERATED = "revenue_generated"
    STORES_VISITED = "stores_visited"
    CONVERSION_RATE = "conversion_rate"


class AchievementProgressStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLAIMED = "claimed"       # Rep clicked claim, awaiting reward hand-out.
    REDEEMED = "redeemed"     # Admin/office confirmed the reward was given.
