from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from enums.user import Role
from middleware.auth import require_any_user, require_office
from repository.category_repo import CategoryRepository
from repository.rep_target_repo import DuplicateRepTargetError, RepTargetRepository
from repository.user_repo import UserRepository
from schemas.rep_target import (
    RepTarget,
    RepTargetCreate,
    RepTargetListResponse,
    RepTargetUpdate,
)
from services.audit_service import record

router = APIRouter(prefix="/rep-targets", tags=["rep-targets"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


def _validate_rep(rep_id):
    """Ensure the target is being set for an active sales rep — office/admin
    can't set a target on themselves or a store user."""
    user = UserRepository.by_id(rep_id)
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Rep not found.")
    if user.get("role") != Role.SALES_REP.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Targets can only be set for users with role 'sales_rep'.",
        )
    if user.get("status") != "active":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Rep account is not active.",
        )
    return user


def _normalize_category_targets(items):
    """Verify each category exists and denormalize its name onto the target
    entry so reports don't need a join. Refuses duplicate category_ids
    within a single target document."""
    seen = set()
    out = []
    for i, ct in enumerate(items or [], start=1):
        if ct.category_id in seen:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Duplicate category_id in category_targets (entry {i}).",
            )
        seen.add(ct.category_id)
        cat = CategoryRepository.by_id(ct.category_id)
        if not cat:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown category_id '{ct.category_id}' in category_targets.",
            )
        out.append(
            {
                "category_id": cat["_id"],
                "category_name": cat["name"],
                "target": float(ct.target),
            }
        )
    return out


@router.get("", response_model=RepTargetListResponse)
async def list_rep_targets(
    rep_id: str | None = Query(None),
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current=Depends(require_any_user),
):
    user = current["user"]
    # Sales rep only sees their own, regardless of rep_id filter.
    effective_rep = rep_id if _is_office(user) else user["_id"]
    skip = (page - 1) * page_size
    items, total = RepTargetRepository.list(
        rep_id=effective_rep, year=year, month=month, skip=skip, limit=page_size
    )
    return RepTargetListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{target_id}", response_model=RepTarget)
async def get_rep_target(target_id: str, current=Depends(require_any_user)):
    tgt = RepTargetRepository.by_id(target_id)
    if not tgt:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    user = current["user"]
    if not _is_office(user) and tgt.get("rep_id") != user["_id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    return tgt


@router.post("", response_model=RepTarget, status_code=status.HTTP_201_CREATED)
async def create_rep_target(
    payload: RepTargetCreate,
    request: Request,
    current=Depends(require_office),
):
    rep = _validate_rep(payload.rep_id)
    normalized = _normalize_category_targets(payload.category_targets)
    try:
        target = RepTargetRepository.insert(
            rep_id=rep["_id"],
            rep_name=rep.get("name"),
            year=payload.year,
            month=payload.month,
            overall_target=payload.overall_target,
            category_targets=normalized,
            actor=current["user"],
        )
    except DuplicateRepTargetError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A target already exists for {rep.get('name') or 'this rep'} "
            f"in {payload.year}-{payload.month:02d}. PATCH it instead.",
        )
    record(
        AuditAction.REP_TARGET_CREATE,
        ResourceType.REP_TARGET,
        resource_id=target["_id"],
        actor=current["user"],
        after={
            "rep_id": target["rep_id"],
            "year": target["year"],
            "month": target["month"],
            "overall_target": target["overall_target"],
            "category_targets_count": len(target["category_targets"]),
        },
        request=request,
    )
    return target


@router.patch("/{target_id}", response_model=RepTarget)
async def update_rep_target(
    target_id: str,
    payload: RepTargetUpdate,
    request: Request,
    current=Depends(require_office),
):
    before = RepTargetRepository.by_id(target_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")

    patch = payload.model_dump(exclude_unset=True)
    if "category_targets" in patch and patch["category_targets"] is not None:
        # Re-normalize so any newly-listed category has its name denormalized
        # and any unknown category is rejected up front.
        patch["category_targets"] = _normalize_category_targets(payload.category_targets)

    after = RepTargetRepository.update(target_id, patch)
    record(
        AuditAction.REP_TARGET_UPDATE,
        ResourceType.REP_TARGET,
        resource_id=target_id,
        actor=current["user"],
        before={k: before.get(k) for k in patch},
        after={k: after.get(k) for k in patch},
        request=request,
    )
    return after


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rep_target(
    target_id: str,
    request: Request,
    current=Depends(require_office),
):
    before = RepTargetRepository.by_id(target_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found")
    RepTargetRepository.delete(target_id)
    record(
        AuditAction.REP_TARGET_DELETE,
        ResourceType.REP_TARGET,
        resource_id=target_id,
        actor=current["user"],
        before={
            "rep_id": before.get("rep_id"),
            "year": before.get("year"),
            "month": before.get("month"),
            "overall_target": before.get("overall_target"),
        },
        request=request,
    )
    return None
