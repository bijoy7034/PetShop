from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from enums.achievement import (
    AchievementMetric,
    AchievementPeriod,
    AchievementProgressStatus,
)


class AchievementReward(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    image: str | None = Field(default=None, max_length=1000)


class AchievementTarget(BaseModel):
    metric: AchievementMetric
    value: float = Field(gt=0)


class SalesAchievementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    reward: AchievementReward
    period: AchievementPeriod
    start_date: datetime
    end_date: datetime
    target: AchievementTarget
    is_active: bool = True


class SalesAchievementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    reward: AchievementReward | None = None
    period: AchievementPeriod | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    target: AchievementTarget | None = None
    is_active: bool | None = None


class SalesAchievement(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    title: str
    description: str
    reward: AchievementReward
    period: AchievementPeriod
    start_date: datetime
    end_date: datetime
    target: AchievementTarget
    is_active: bool = True
    created_by_id: str | None = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime


class SalesAchievementListResponse(BaseModel):
    items: list[SalesAchievement]
    total: int
    page: int
    page_size: int


class SalesAchievementProgress(BaseModel):
    """Per-rep progress against an achievement. current_value is
    recomputed from underlying orders/visits on read; status transitions
    only on `claim`."""
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    achievement_id: str
    achievement_title: str | None = None
    achievement_metric: str | None = None
    achievement_period: str | None = None
    reward: AchievementReward | None = None
    sales_rep_id: str
    sales_rep_name: str | None = None
    current_value: float = 0.0
    target_value: float
    status: AchievementProgressStatus = AchievementProgressStatus.IN_PROGRESS
    completed_at: datetime | None = None
    claimed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MyAchievementsResponse(BaseModel):
    items: list[SalesAchievementProgress]
    total: int
