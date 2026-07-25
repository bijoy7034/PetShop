from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.achievement import (
    AchievementMetric,
    AchievementProgressStatus,
)
from enums.audit import AuditAction, ResourceType
from helpers.datetime import now_utc
from middleware.auth import require_any_user, require_office, require_sales_rep
from repository.sales_achievement_progress_repo import (
    SalesAchievementProgressRepository,
)
from repository.sales_achievement_repo import SalesAchievementRepository
from repository.user_repo import UserRepository
from schemas.sales_achievement import (
    AchievementProgressListResponse,
    AchievementRedeem,
    MyAchievementsResponse,
    SalesAchievement,
    SalesAchievementCreate,
    SalesAchievementListResponse,
    SalesAchievementProgress,
    SalesAchievementUpdate,
)
from services import notification_service, rep_analytics_service as analytics
from services.audit_service import record

router = APIRouter(prefix="/achievements", tags=["achievements"])


def _current_value(rep_id, metric, start, end):
    """Recompute the rep's current value for a given metric across the
    achievement window. Uses the same aggregation the analytics service
    already provides."""
    m = str(metric)
    if m == AchievementMetric.ORDERS_PLACED.value:
        # Separate query — this metric counts orders regardless of
        # acceptance status, so _rep_totals (counted-statuses only) is
        # the wrong shape.
        return float(analytics.orders_placed_by_rep(rep_id, start, end))
    totals = analytics._rep_totals(rep_id, start, end)
    if m == AchievementMetric.ORDERS_COMPLETED.value:
        return float(totals["orders"])
    if m == AchievementMetric.REVENUE_GENERATED.value:
        return float(totals["revenue"])
    if m == AchievementMetric.STORES_VISITED.value:
        return float(totals["unique_stores_visited"])
    if m == AchievementMetric.CONVERSION_RATE.value:
        # Multiply by 100 so a "80 conversion" target reads as 80%.
        return float(
            (totals["orders"] / totals["visits"] * 100)
            if totals["visits"] else 0.0
        )
    return 0.0


_TERMINAL_STATUSES = {
    AchievementProgressStatus.CLAIMED.value,
    AchievementProgressStatus.REDEEMED.value,
}


def _hydrate_progress(progress, achievement):
    """Recompute current_value from underlying data and flag completed
    status so the response is always fresh. Fires a one-shot notification
    the first time a row crosses its target (marker: no completed_at yet)."""
    metric = (achievement.get("target") or {}).get("metric")
    target_value = float((achievement.get("target") or {}).get("value") or 0)
    current = _current_value(
        progress["sales_rep_id"], metric,
        achievement.get("start_date"), achievement.get("end_date"),
    )
    progress["current_value"] = round(current, 2)
    progress["target_value"] = target_value
    # Don't downgrade a claimed/redeemed row.
    if progress.get("status") not in _TERMINAL_STATUSES:
        if current >= target_value:
            progress["status"] = AchievementProgressStatus.COMPLETED.value
            first_crossing = not progress.get("completed_at")
            if first_crossing:
                progress["completed_at"] = now_utc()
                # Persist the crossing so we don't fire the notification twice.
                SalesAchievementProgressRepository.set_value(
                    progress["_id"],
                    current_value=progress["current_value"],
                    status=progress["status"],
                    completed_at=progress["completed_at"],
                )
                notification_service.notify_achievement_completed(
                    sales_rep={
                        "_id": progress["sales_rep_id"],
                        "name": progress.get("sales_rep_name"),
                    },
                    achievement=achievement,
                    progress_id=progress["_id"],
                )
        else:
            progress["status"] = AchievementProgressStatus.IN_PROGRESS.value
    return progress


# --------- Admin/office manage the SalesAchievement catalogue ---------

@router.get("", response_model=SalesAchievementListResponse)
async def list_achievements(
    is_active: bool | None = Query(None),
    period: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(require_any_user),
):
    skip = (page - 1) * page_size
    items, total = SalesAchievementRepository.list(
        is_active=is_active, period=period, skip=skip, limit=page_size
    )
    return SalesAchievementListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/mine", response_model=MyAchievementsResponse)
async def my_achievements(current=Depends(require_sales_rep)):
    """Sales rep's own progress. Auto-hydrates rows for every currently-
    active achievement so a rep who just signed up still sees them."""
    user = current["user"]
    now = now_utc()
    actives = SalesAchievementRepository.list_active_at(now)
    for a in actives:
        SalesAchievementProgressRepository.ensure_row(
            achievement=a, sales_rep=user
        )
    rows = SalesAchievementProgressRepository.list_by_rep(user["_id"])
    # Recompute current_value + status from underlying data.
    ach_by_id = {a["_id"]: a for a in actives}
    hydrated = []
    for r in rows:
        a = ach_by_id.get(r["achievement_id"]) or SalesAchievementRepository.by_id(
            r["achievement_id"]
        )
        if not a:
            continue
        hydrated.append(_hydrate_progress(r, a))
    return {"items": hydrated, "total": len(hydrated)}


@router.get("/{achievement_id}", response_model=SalesAchievement)
async def get_achievement(achievement_id: str, _=Depends(require_any_user)):
    a = SalesAchievementRepository.by_id(achievement_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Achievement not found")
    return a


@router.post("", response_model=SalesAchievement, status_code=status.HTTP_201_CREATED)
async def create_achievement(
    payload: SalesAchievementCreate,
    request: Request,
    current=Depends(require_office),
):
    if payload.end_date < payload.start_date:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "end_date cannot be before start_date."
        )
    a = SalesAchievementRepository.insert(
        title=payload.title,
        description=payload.description,
        reward=payload.reward.model_dump(),
        period=payload.period.value,
        start_date=payload.start_date,
        end_date=payload.end_date,
        target=payload.target.model_dump(),
        is_active=payload.is_active,
        actor=current["user"],
    )
    # Auto-create progress rows for every active sales rep so they'll
    # see the new achievement on their next /mine call.
    reps, _ = UserRepository.list(role="sales_rep", status="active", limit=1000)
    for rep in reps:
        SalesAchievementProgressRepository.ensure_row(
            achievement=a, sales_rep=rep
        )
    record(
        AuditAction.ACHIEVEMENT_CREATE,
        ResourceType.ACHIEVEMENT,
        resource_id=a["_id"],
        actor=current["user"],
        after={"title": a["title"], "metric": a["target"]["metric"], "value": a["target"]["value"]},
        request=request,
    )
    return a


@router.patch("/{achievement_id}", response_model=SalesAchievement)
async def update_achievement(
    achievement_id: str,
    payload: SalesAchievementUpdate,
    request: Request,
    current=Depends(require_office),
):
    before = SalesAchievementRepository.by_id(achievement_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Achievement not found")
    patch = payload.model_dump(exclude_unset=True)
    # Unwrap the enum values for storage.
    if "period" in patch and hasattr(patch["period"], "value"):
        patch["period"] = patch["period"].value
    if "target" in patch and patch["target"] is not None:
        # Ensure metric is stored as its string value.
        if hasattr(patch["target"].get("metric"), "value"):
            patch["target"]["metric"] = patch["target"]["metric"].value
    after = SalesAchievementRepository.update(achievement_id, patch)
    record(
        AuditAction.ACHIEVEMENT_UPDATE,
        ResourceType.ACHIEVEMENT,
        resource_id=achievement_id,
        actor=current["user"],
        before={k: before.get(k) for k in patch},
        after={k: after.get(k) for k in patch},
        request=request,
    )
    return after


@router.delete("/{achievement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_achievement(
    achievement_id: str,
    request: Request,
    current=Depends(require_office),
):
    before = SalesAchievementRepository.by_id(achievement_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Achievement not found")
    SalesAchievementRepository.delete(achievement_id)
    # Also clean up per-rep progress rows.
    SalesAchievementProgressRepository.delete_by_achievement(achievement_id)
    record(
        AuditAction.ACHIEVEMENT_DELETE,
        ResourceType.ACHIEVEMENT,
        resource_id=achievement_id,
        actor=current["user"],
        before={"title": before.get("title")},
        request=request,
    )
    return None


@router.post("/{achievement_id}/claim", response_model=SalesAchievement)
async def claim_achievement(
    achievement_id: str,
    request: Request,
    current=Depends(require_sales_rep),
):
    """Sales rep claims a completed achievement. Re-verifies the current
    value against the target at claim time (defence against stale reads)."""
    user = current["user"]
    a = SalesAchievementRepository.by_id(achievement_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Achievement not found")
    if not a.get("is_active"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This achievement is no longer active.",
        )
    progress = SalesAchievementProgressRepository.ensure_row(
        achievement=a, sales_rep=user
    )
    hydrated = _hydrate_progress(progress, a)
    if hydrated["status"] in (
        AchievementProgressStatus.CLAIMED.value,
        AchievementProgressStatus.REDEEMED.value,
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Already {hydrated['status']}.",
        )
    if hydrated["status"] != AchievementProgressStatus.COMPLETED.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Not eligible to claim yet — current {hydrated['current_value']} "
            f"of target {hydrated['target_value']}.",
        )
    # Persist the claim event.
    SalesAchievementProgressRepository.mark_claimed(progress["_id"])
    record(
        AuditAction.ACHIEVEMENT_CLAIM,
        ResourceType.ACHIEVEMENT,
        resource_id=achievement_id,
        actor=user,
        after={"current_value": hydrated["current_value"], "target_value": hydrated["target_value"]},
        request=request,
    )
    notification_service.notify_achievement_claimed(
        sales_rep=user, achievement=a, progress_id=progress["_id"],
    )
    return a


@router.get(
    "/{achievement_id}/progress",
    response_model=AchievementProgressListResponse,
)
async def list_achievement_progress(
    achievement_id: str,
    status_filter: str | None = Query(
        None, alias="status",
        description="in_progress | completed | claimed | redeemed",
    ),
    hydrate: bool = Query(
        True,
        description="Recompute current_value from live orders/visits per row. "
                    "Costs one aggregation per row; set false for a raw list "
                    "of only the persisted state.",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(require_office),
):
    """Office/admin view of every rep's progress on one achievement.
    Filter by `status=claimed` to see the queue waiting to be redeemed.

    By default, `current_value` on every row is recomputed live so the
    office view matches what each rep sees — same source of truth as
    GET /achievements/mine. Set `hydrate=false` to skip re-aggregation."""
    a = SalesAchievementRepository.by_id(achievement_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Achievement not found")
    items, total = SalesAchievementProgressRepository.list_by_achievement(
        achievement_id,
        status=status_filter,
        skip=(page - 1) * page_size,
        limit=page_size,
    )
    if hydrate:
        items = [_hydrate_progress(row, a) for row in items]
    return AchievementProgressListResponse(
        items=items, total=total, page=page, page_size=page_size,
    )


@router.post(
    "/progress/{progress_id}/redeem",
    response_model=SalesAchievementProgress,
)
async def redeem_achievement(
    progress_id: str,
    payload: AchievementRedeem,
    request: Request,
    current=Depends(require_office),
):
    """Admin/office marks a claimed reward as physically delivered. Only
    a `claimed` row can be redeemed; `in_progress`, `completed`, and
    already-`redeemed` rows are 400. Emits a notification back to the
    rep so their app updates in the next poll."""
    progress = SalesAchievementProgressRepository.by_id(progress_id)
    if not progress:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Progress row not found")
    if progress["status"] != AchievementProgressStatus.CLAIMED.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Only claimed rewards can be redeemed. This row is "
            f"'{progress['status']}'.",
        )
    achievement = SalesAchievementRepository.by_id(progress["achievement_id"])
    if not achievement:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Achievement no longer exists — cannot redeem.",
        )

    updated = SalesAchievementProgressRepository.mark_redeemed(
        progress_id, actor=current["user"], notes=payload.notes,
    )
    record(
        AuditAction.ACHIEVEMENT_REDEEM,
        ResourceType.ACHIEVEMENT,
        resource_id=progress["achievement_id"],
        actor=current["user"],
        before={"progress_id": progress_id, "sales_rep_id": progress["sales_rep_id"]},
        after={"notes": payload.notes},
        request=request,
    )
    notification_service.notify_achievement_redeemed(
        sales_rep={
            "_id": progress["sales_rep_id"],
            "name": progress.get("sales_rep_name"),
        },
        achievement=achievement,
        progress_id=progress_id,
        actor=current["user"],
    )
    return updated
