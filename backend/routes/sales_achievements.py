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
    MyAchievementsResponse,
    SalesAchievement,
    SalesAchievementCreate,
    SalesAchievementListResponse,
    SalesAchievementUpdate,
)
from services import rep_analytics_service as analytics
from services.audit_service import record

router = APIRouter(prefix="/achievements", tags=["achievements"])


def _current_value(rep_id, metric, start, end):
    """Recompute the rep's current value for a given metric across the
    achievement window. Uses the same aggregation the analytics service
    already provides."""
    totals = analytics._rep_totals(rep_id, start, end)
    m = str(metric)
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


def _hydrate_progress(progress, achievement):
    """Recompute current_value from underlying data and flag completed
    status so the response is always fresh."""
    metric = (achievement.get("target") or {}).get("metric")
    target_value = float((achievement.get("target") or {}).get("value") or 0)
    current = _current_value(
        progress["sales_rep_id"], metric,
        achievement.get("start_date"), achievement.get("end_date"),
    )
    progress["current_value"] = round(current, 2)
    progress["target_value"] = target_value
    # Don't downgrade an already-claimed row.
    if progress.get("status") != AchievementProgressStatus.CLAIMED.value:
        if current >= target_value:
            progress["status"] = AchievementProgressStatus.COMPLETED.value
            if not progress.get("completed_at"):
                progress["completed_at"] = now_utc()
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
    if hydrated["status"] == AchievementProgressStatus.CLAIMED.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already claimed."
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
    return a
