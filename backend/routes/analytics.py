from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from middleware.auth import require_any_user, require_office
from repository.inventory_repo import InventoryRepository
from repository.order_repo import OrderRepository
from repository.rep_target_repo import RepTargetRepository
from repository.store_repo import StoreRepository
from repository.user_repo import UserRepository
from schemas.analytics import (
    AdminDashboard,
    DistrictAnalytics,
    DistrictAnalyticsDetail,
    DistrictsSummary,
    MonthlyRepAnalytics,
    RankedLeaderboard,
    RepAnalytics,
    RepDashboard,
    StaffDashboard,
    TargetAchievement,
)
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


from middleware.auth import require_admin  # noqa: E402  (kept beside dashboard routes)


@router.get("/admin-dashboard", response_model=AdminDashboard)
async def admin_dashboard(current=Depends(require_admin)):
    """One-shot admin home screen: user greeting, pending-action
    counters, and rolled-up KPIs for the current calendar month."""
    user = current["user"]
    now = datetime.now(timezone.utc)
    return {
        "user_greeting": {
            "display_name": user.get("name"),
            "email": user.get("email"),
            "role": user.get("role"),
        },
        "pending_action_counts": {
            "pending_order_approvals": OrderRepository.count_by_status(
                "pending_admin_approval"),
            "pending_store_approvals": StoreRepository.count_by_status("pending"),
            "credit_limit_requests": StoreRepository.pending_credit_change_count(),
            "low_stock_variants": InventoryRepository.low_stock_count(),
        },
        "high_level_kpis": {
            "total_monthly_revenue": OrderRepository.revenue_for_month(
                now.year, now.month),
            "active_districts_count": StoreRepository.active_districts_count(),
            "total_stores_reached": OrderRepository.distinct_stores_reached(),
            "total_active_reps": UserRepository.count_by_role(
                "sales_rep", active_only=True),
        },
    }


@router.get("/rep-dashboard", response_model=RepDashboard)
async def rep_dashboard(
    rep_id: str | None = Query(None),
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    current=Depends(require_any_user),
):
    """Rep home screen: store counts, order counts, and progress
    against this month's RepTarget. Sales rep is scoped to themselves
    regardless of the query param."""
    user = current["user"]
    effective_rep = rep_id if _is_office(user) else user["_id"]
    now = datetime.now(timezone.utc)
    y = year or now.year
    m = month or now.month

    # Store counts — reuse rep_summary aggregation.
    rs = StoreRepository.rep_summary(rep_id=effective_rep)

    # Order counts.
    pending_approval = OrderRepository.count_by_status(
        "pending_admin_approval", sales_rep_id=effective_rep)
    in_transit = OrderRepository.count_by_statuses(
        ("accepted", "packing", "out_for_delivery"),
        sales_rep_id=effective_rep,
    )
    delivered_this_month = OrderRepository.delivered_count_for_month(
        y, m, sales_rep_id=effective_rep)

    # Target progress.
    tgt_doc = RepTargetRepository.by_rep_month(effective_rep, y, m)
    target_amount = float((tgt_doc or {}).get("overall_target") or 0)
    achieved = OrderRepository.revenue_for_month(
        y, m, sales_rep_id=effective_rep)
    pct = round(achieved / target_amount * 100, 2) if target_amount else 0.0

    # Look up the rep's display name.
    rep_doc = UserRepository.by_id(effective_rep) if effective_rep else None

    return {
        "user_greeting": {
            "display_name": (rep_doc or user).get("name"),
            "email": (rep_doc or user).get("email"),
            "role": (rep_doc or user).get("role"),
        },
        "store_counts": {
            "approved_stores": rs["approved_stores_count"],
            "pending_stores": rs["pending_stores_count"],
            "rejected_stores": rs["rejected_stores_count"],
        },
        "order_counts": {
            "pending_approval": pending_approval,
            "in_transit": in_transit,
            "delivered_this_month": delivered_this_month,
        },
        "target_progress": {
            "target_amount": target_amount,
            "achieved_amount": round(achieved, 2),
            "percentage": pct,
        },
    }


@router.get("/staff-dashboard", response_model=StaffDashboard)
async def staff_dashboard(current=Depends(require_office)):
    """Office-staff home: operational work queue."""
    return {
        "operational_queue": {
            "pending_store_reviews": StoreRepository.count_by_status("pending"),
            "credit_review_requests": StoreRepository.pending_credit_change_count(),
            "orders_needing_packing": OrderRepository.count_by_status("accepted"),
            "orders_ready_for_dispatch": OrderRepository.count_by_status("packing"),
            "low_stock_items": InventoryRepository.low_stock_count(),
        },
    }


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


@router.get("/leaderboard", response_model=RankedLeaderboard)
async def leaderboard_endpoint(
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    _=Depends(require_any_user),
):
    """Ranked rep leaderboard for a calendar month. Sorted by
    total_sales_volume descending; ranks are 1-indexed. Each entry
    includes orders_count and target_achievement_pct (0 when the rep
    has no RepTarget for that month). Accessible to every authenticated
    user — sales reps see the same rankings as office/admin so they
    know where they stand.

    `year` + `month` default to the current calendar month.
    """
    now = datetime.now(timezone.utc)
    y = year or now.year
    m = month or now.month
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = (datetime(y + (m // 12), (m % 12) + 1, 1, tzinfo=timezone.utc)
           if m < 12 else datetime(y + 1, 1, 1, tzinfo=timezone.utc))
    end -= timedelta(microseconds=1)

    # Reuse the existing service — it already gives one row per active rep
    # with revenue + orders + visits. Ignore visits here.
    entries = analytics.leaderboard(from_dt=start, to_dt=end, sort="revenue", limit=500)

    from repository.rep_target_repo import RepTargetRepository
    rankings = []
    for e in entries:
        target_doc = RepTargetRepository.by_rep_month(e["rep_id"], y, m)
        target = float((target_doc or {}).get("overall_target") or 0)
        pct = round(e["revenue"] / target * 100, 2) if target else 0.0
        rankings.append({
            "rank": 0,  # assigned after final sort
            "sales_rep_id": e["rep_id"],
            "sales_rep_name": e.get("rep_name"),
            "total_sales_volume": float(e.get("revenue") or 0),
            "orders_count": int(e.get("orders") or 0),
            "target_achievement_pct": pct,
        })
    rankings.sort(key=lambda r: r["total_sales_volume"], reverse=True)
    for i, r in enumerate(rankings, start=1):
        r["rank"] = i

    return {"period": f"{y:04d}-{m:02d}", "rankings": rankings}


@router.get("/districts-summary", response_model=DistrictsSummary)
async def districts_summary(current=Depends(require_office)):
    """District-wise breakdown for the current month: approved-store
    count, revenue booked this month, and total credit exposure.
    Sorted by monthly revenue desc. Office/admin only."""
    now = datetime.now(timezone.utc)
    m_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    m_end = (
        datetime(
            now.year + (now.month // 12),
            (now.month % 12) + 1, 1, tzinfo=timezone.utc,
        ) if now.month < 12 else datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    )
    m_end -= timedelta(microseconds=1)
    return StoreRepository.districts_summary(
        month_start=m_start, month_end=m_end,
    )


@router.get("/districts", response_model=DistrictAnalytics)
async def district_analytics_endpoint(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    top_n_products: int = Query(5, ge=1, le=20),
    current=Depends(require_any_user),
):
    """Revenue + orders per district, with each district's top-N most-ordered
    (product, variant) pairs. Sales rep sees only districts where they have
    assigned stores; office/admin see every district."""
    user = current["user"]
    allowed = None
    if not _is_office(user):
        rep_districts = StoreRepository.districts_for_rep(user["_id"])
        allowed = set(rep_districts) or set()  # empty set → no rows

    items = analytics.district_analytics(
        from_dt=from_, to_dt=to,
        allowed_districts=allowed,
        top_n_products=top_n_products,
    )
    return {"range": {"from": from_, "to": to}, "items": items}


@router.get(
    "/districts/{district_name}",
    response_model=DistrictAnalyticsDetail,
)
async def district_detail_endpoint(
    district_name: str,
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    top_n_products: int = Query(20, ge=1, le=50),
    top_n_stores: int = Query(20, ge=1, le=50),
    top_n_reps: int = Query(20, ge=1, le=50),
    current=Depends(require_any_user),
):
    """Deep view of one district — totals, top products, revenue by
    category, top stores, revenue by rep. Sales rep can only pull a
    district where they have at least one assigned store (404 otherwise
    so we don't leak the district list)."""
    user = current["user"]
    if not _is_office(user):
        allowed = set(StoreRepository.districts_for_rep(user["_id"]))
        if district_name not in allowed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "District not found")

    detail = analytics.district_detail(
        district_name,
        from_dt=from_, to_dt=to,
        top_n_products=top_n_products,
        top_n_stores=top_n_stores,
        top_n_reps=top_n_reps,
    )
    if detail is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No orders found in '{district_name}' for the given range.",
        )
    detail["range"] = {"from": from_, "to": to}
    return detail
