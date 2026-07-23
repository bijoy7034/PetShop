from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from middleware.auth import require_any_user, require_office
from schemas.analytics import Leaderboard, MonthlyRepAnalytics, RepAnalytics, TargetAchievement
from services import rep_analytics_service as analytics


router = APIRouter(prefix="/analytics", tags=["analytics"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


def _can_view_rep(user, rep_id):
    """Sales rep can only view their own analytics. Office/admin can
    view anyone's."""
    if _is_office(user):
        return True
    return user["_id"] == rep_id


def _range_from_flag(range_flag, from_dt=None, to_dt=None):
    now = datetime.now(timezone.utc)
    if range_flag == "current_week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, now
    if range_flag == "current_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now
    return from_dt, to_dt


@router.get("/rep/{rep_id}", response_model=RepAnalytics)
async def rep_analytics_endpoint(
    rep_id: str,
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    current=Depends(require_any_user),
):
    """Every metric — totals + ratios — over the given date range.
    Both `from` and `to` are optional; omitting both yields lifetime.
    Sales rep sees only own; office/admin see any rep."""
    if not _can_view_rep(current["user"], rep_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rep not found")
    return analytics.rep_analytics(rep_id, from_dt=from_, to_dt=to)


@router.get("/rep/{rep_id}/monthly", response_model=MonthlyRepAnalytics)
async def rep_analytics_monthly(
    rep_id: str,
    year: int = Query(..., ge=2000, le=2100),
    current=Depends(require_any_user),
):
    """Trend view: totals + ratios per month for the given calendar year."""
    if not _can_view_rep(current["user"], rep_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rep not found")
    return analytics.monthly_trend(rep_id, year)


@router.get("/rep/{rep_id}/target-achievement", response_model=TargetAchievement)
async def rep_target_achievement(
    rep_id: str,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    current=Depends(require_any_user),
):
    """Current-month achievement vs the RepTarget for that month.
    Includes category-wise breakdown when the target doc has
    category_targets."""
    if not _can_view_rep(current["user"], rep_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rep not found")
    return analytics.target_achievement(rep_id, year, month)


@router.get("/leaderboard", response_model=Leaderboard)
async def leaderboard_endpoint(
    sort: str = Query(
        "revenue",
        description="revenue | orders | visits | conversion_rate | "
                    "avg_order_value | target_achievement_pct | "
                    "monthly_revenue | monthly_orders",
    ),
    range_flag: str | None = Query(
        None, alias="range",
        description="current_week | current_month | custom (with from/to)",
    ),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    limit: int = Query(100, ge=1, le=500),
    current=Depends(require_office),
):
    """Cross-rep ranking. Sales reps don't see the leaderboard directly —
    office/admin only. Some sort keys (target_achievement_pct,
    monthly_revenue, monthly_orders) require year+month to compute
    per-rep target percentages."""
    dt_from, dt_to = _range_from_flag(range_flag, from_, to)
    entries = analytics.leaderboard(from_dt=dt_from, to_dt=dt_to, sort=sort, limit=limit)

    # target_achievement_pct requires knowing which month to compare
    # against a RepTarget. If year+month were provided, resolve it now.
    if sort == "target_achievement_pct" and year and month:
        from repository.rep_target_repo import RepTargetRepository
        for e in entries:
            t = RepTargetRepository.by_rep_month(e["rep_id"], year, month)
            target = float((t or {}).get("overall_target") or 0)
            e["target"] = target
            e["target_achievement_pct"] = (
                round(e["revenue"] / target * 100, 2) if target else None
            )
        entries.sort(
            key=lambda e: (e["target_achievement_pct"] or -1),
            reverse=True,
        )

    return {
        "range": {"from": dt_from, "to": dt_to},
        "sort": sort,
        "items": entries,
    }
