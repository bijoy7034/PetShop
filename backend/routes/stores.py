from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from enums.store import StoreStatus
from enums.user import Role
from middleware.auth import require_admin, require_any_user, require_office
from repository.store_repo import StoreRepository
from repository.user_repo import UserRepository
from schemas.analytics import (
    CreditViolationsReport,
    RecommendedProductsResponse,
    StoreCreditReport,
)
from schemas.store import (
    CreditLimitPropose,
    CreditLimitReject,
    OverCreditReport,
    RepStoreSummary,
    Store,
    StoreApprove,
    StoreAssign,
    StoreCreate,
    StoreInfoResponse,
    StoreListResponse,
    StoreReject,
    StoreUpdate,
    StoresCreditSummary,
)
from services import rep_analytics_service as analytics
from services.audit_service import record

router = APIRouter(prefix="/stores", tags=["stores"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


def _visible(user, store):
    """Sales rep sees only their assigned stores. Office / admin see all."""
    if _is_office(user):
        return True
    return store.get("sales_rep_id") == user["_id"]


@router.get("", response_model=StoreListResponse)
async def list_stores(
    ids: str | None = Query(
        None,
        description="Comma-separated store _ids to hydrate a specific set.",
    ),
    status_filter: str | None = Query(
        None, alias="status",
        description="Single status OR comma-separated list.",
    ),
    credit_change_status: str | None = Query(
        None,
        description="Filter by pending credit-limit changes: 'pending' | 'none' | 'approved' | 'rejected'.",
    ),
    search: str | None = Query(
        None,
        description="Case-insensitive substring across name / location / gst_number / store code.",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current=Depends(require_any_user),
):
    from helpers.query import csv_list
    skip = (page - 1) * page_size
    sales_rep_id = None if _is_office(current["user"]) else current["user"]["_id"]
    items, total = StoreRepository.list(
        sales_rep_id=sales_rep_id,
        ids=csv_list(ids),
        statuses=csv_list(status_filter),
        credit_change_status=credit_change_status,
        search=search,
        skip=skip,
        limit=page_size,
    )
    return StoreListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/rep-summary", response_model=RepStoreSummary)
async def rep_store_summary(
    rep_id: str | None = Query(
        None,
        description="Office/admin can pass any rep id; sales_rep is force-scoped to their own.",
    ),
    current=Depends(require_any_user),
):
    """Assigned-store counts + rolled-up credit metrics for a rep's
    book of business. Overdue amount comes from underlying orders — an
    order is overdue when its payment_due_date < now and it isn't fully
    paid. If a sales_rep calls this, `rep_id` is force-scoped to their
    own id regardless of what they pass."""
    user = current["user"]
    effective_rep = rep_id if _is_office(user) else user["_id"]
    return StoreRepository.rep_summary(rep_id=effective_rep)


@router.get("/info-dashboard", response_model=StoreInfoResponse)
async def store_info_dashboard(
    rep_id: str | None = Query(
        None,
        description="Office/admin can pass any rep id; sales_rep is force-scoped to their own book.",
    ),
    current=Depends(require_any_user),
):
    """Grid of the caller's stores with credit tile + per-status
    order counts. Sales rep is force-scoped to their own assigned
    stores; office/admin can pass any rep_id or omit for global view."""
    user = current["user"]
    effective_rep = rep_id if _is_office(user) else user["_id"]
    return StoreRepository.store_info_dashboard(sales_rep_id=effective_rep)


@router.get("/over-credit-report", response_model=OverCreditReport)
async def over_credit_report(
    district: str | None = Query(None),
    search: str | None = Query(None),
    _=Depends(require_office),
):
    """Every approved store where credit_utilized > credit_limit
    (equivalently: available_credit < 0). Sorted worst-first."""
    return StoreRepository.over_credit_report(
        district=district, search=search,
    )


@router.get("/credit-violations-report", response_model=CreditViolationsReport)
async def credit_violations_report(
    district: str | None = Query(None),
    search: str | None = Query(None),
    _=Depends(require_office),
):
    """Detailed per-store violation queue with computed health_score
    (0-100, higher = healthier). Sorted worst-first."""
    return StoreRepository.credit_violations_report(
        district=district, search=search)


@router.get("/credit-summary", response_model=StoresCreditSummary)
async def stores_credit_summary(
    district: str | None = Query(None),
    search: str | None = Query(None),
    _=Depends(require_office),
):
    """Portfolio credit view across every approved store. Office/admin
    only — sales reps don't see the cross-portfolio number.
    `violations_breakdown` splits stores into three buckets:
      - limit_exceeded_count: credit_used > credit_limit but no overdue
      - period_overdue_count: overdue payments but within credit limit
      - both_exceeded_count: both conditions true simultaneously"""
    return StoreRepository.credit_summary(district=district, search=search)


@router.get("/{store_id}", response_model=Store)
async def get_store(store_id: str, current=Depends(require_any_user)):
    store = StoreRepository.by_id(store_id)
    if not store or not _visible(current["user"], store):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    return store


from datetime import datetime as _dt  # noqa: E402


@router.get("/{store_id}/credit-report", response_model=StoreCreditReport)
async def store_credit_report(
    store_id: str,
    start_date: _dt | None = Query(None),
    end_date: _dt | None = Query(None),
    current=Depends(require_any_user),
):
    """Per-store credit statement + transaction log. Sales rep can
    only pull this for their assigned stores."""
    store = StoreRepository.by_id(store_id)
    if not store or not _visible(current["user"], store):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    report = StoreRepository.store_credit_report(
        store_id, start_date=start_date, end_date=end_date,
    )
    if not report:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    return report


@router.post("", response_model=Store, status_code=status.HTTP_201_CREATED)
async def create_store(
    payload: StoreCreate,
    request: Request,
    current=Depends(require_any_user),
):
    """Create a store.

    - **sales_rep** callers self-assign and always land in `pending` with
      credit_limit=0. Payload's `sales_rep_id` / `credit_limit` fields are
      ignored (they can't reassign or set their own limit).
    - **admin / office_staff** callers must specify `sales_rep_id`. The
      store is created as `approved` with the supplied `credit_limit`
      (default 0) — no separate approval step is needed since the
      approver is the one creating it.
    """
    user = current["user"]
    role = user["role"]

    if role == Role.SALES_REP.value:
        # Sales reps can't set their own credit limit or reassign the store.
        if payload.sales_rep_id is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Sales reps can't set sales_rep_id — you are automatically the assigned rep.",
            )
        if payload.credit_limit is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Sales reps can't set credit_limit — office/admin sets it on approval.",
            )
        store = StoreRepository.insert(
            sales_rep_id=user["_id"],
            sales_rep_name=user.get("name"),
            name=payload.name,
            location=payload.location,
            district=payload.district,
            contact=payload.contact.model_dump(),
            geo=payload.geo.model_dump(),
            email=payload.email,
            gst_number=payload.gst_number,
            notes=payload.notes,
        )
        record(
            AuditAction.STORE_CREATE,
            ResourceType.STORE,
            resource_id=store["_id"],
            actor=user,
            after={"name": store["name"], "location": store["location"]},
            request=request,
        )
        return store

    # admin / office_staff path — must specify the sales rep to assign.
    if not payload.sales_rep_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "sales_rep_id is required when admin or office_staff creates a store.",
        )
    target = UserRepository.by_id(payload.sales_rep_id)
    if not target:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Assigned user not found")
    if target.get("role") != Role.SALES_REP.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Assigned user must be a sales representative.",
        )
    if target.get("status") != "active":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Assigned sales rep account is not active.",
        )

    credit_limit = float(payload.credit_limit or 0)
    credit_utilized = float(payload.credit_utilized or 0)
    if credit_utilized > credit_limit:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"credit_utilized ({credit_utilized}) cannot exceed credit_limit ({credit_limit}) at store creation.",
        )
    store = StoreRepository.insert(
        sales_rep_id=target["_id"],
        sales_rep_name=target.get("name"),
        name=payload.name,
        location=payload.location,
        district=payload.district,
        contact=payload.contact.model_dump(),
        geo=payload.geo.model_dump(),
        email=payload.email,
        gst_number=payload.gst_number,
        notes=payload.notes,
        status=StoreStatus.APPROVED.value,
        credit_limit=credit_limit,
        credit_used=credit_utilized,
        credit_period_days=payload.credit_period_days,
        is_free_cancellation=payload.is_free_cancellation,
        cancellation_charges=payload.cancellation_charges,
        return_window_days=payload.return_window_days,
    )
    # Two audit rows: created AND approved-in-one-shot, so the audit trail
    # explains the store landing as approved without an approval step.
    record(
        AuditAction.STORE_CREATE,
        ResourceType.STORE,
        resource_id=store["_id"],
        actor=user,
        after={
            "name": store["name"],
            "location": store["location"],
            "sales_rep_id": store["sales_rep_id"],
        },
        request=request,
    )
    record(
        AuditAction.STORE_APPROVE,
        ResourceType.STORE,
        resource_id=store["_id"],
        actor=user,
        before={"status": "pending", "credit_limit": 0.0},
        after={"status": store["status"], "credit_limit": store["credit_limit"]},
        request=request,
    )
    return store


@router.patch("/{store_id}", response_model=Store)
async def update_store(
    store_id: str,
    payload: StoreUpdate,
    request: Request,
    current=Depends(require_any_user),
):
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    user = current["user"]
    if not _is_office(user) and store.get("sales_rep_id") != user["_id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")

    patch = payload.model_dump(exclude_unset=True)
    if "contact" in patch and patch["contact"] is not None:
        patch["contact"] = payload.contact.model_dump()
    if "geo" in patch and patch["geo"] is not None:
        patch["geo"] = payload.geo.model_dump()

    after = StoreRepository.update(store_id, patch)
    record(
        AuditAction.STORE_UPDATE,
        ResourceType.STORE,
        resource_id=store_id,
        actor=user,
        before={k: store.get(k) for k in patch},
        after={k: after.get(k) for k in patch},
        request=request,
    )
    return after


@router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(
    store_id: str,
    request: Request,
    current=Depends(require_office),
):
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    StoreRepository.delete(store_id)
    record(
        AuditAction.STORE_DELETE,
        ResourceType.STORE,
        resource_id=store_id,
        actor=current["user"],
        before={"name": store["name"], "sales_rep_id": store.get("sales_rep_id")},
        request=request,
    )
    return None


@router.post("/{store_id}/approve", response_model=Store)
async def approve_store(
    store_id: str,
    payload: StoreApprove,
    request: Request,
    current=Depends(require_office),
):
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    after = StoreRepository.approve(store_id, payload.credit_limit)
    record(
        AuditAction.STORE_APPROVE,
        ResourceType.STORE,
        resource_id=store_id,
        actor=current["user"],
        before={
            "status": store["status"],
            "credit_limit": store.get("credit_limit"),
        },
        after={"status": after["status"], "credit_limit": after["credit_limit"]},
        request=request,
    )
    return after


@router.post("/{store_id}/reject", response_model=Store)
async def reject_store(
    store_id: str,
    payload: StoreReject,
    request: Request,
    current=Depends(require_office),
):
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    after = StoreRepository.reject(store_id, payload.reason)
    record(
        AuditAction.STORE_REJECT,
        ResourceType.STORE,
        resource_id=store_id,
        actor=current["user"],
        before={"status": store["status"]},
        after={"status": after["status"], "reject_reason": payload.reason},
        request=request,
    )
    return after


@router.post("/{store_id}/assign", response_model=Store)
async def assign_store(
    store_id: str,
    payload: StoreAssign,
    request: Request,
    current=Depends(require_office),
):
    """Reassign a store to a different sales rep. Admin/office only. The
    target must be an active sales_rep."""
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    target = UserRepository.by_id(payload.sales_rep_id)
    if not target:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Target user not found")
    if target.get("role") != "sales_rep":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Target user is not a sales representative.",
        )
    if target.get("status") != "active":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Target sales rep account is not active.",
        )

    after = StoreRepository.assign(store_id, target["_id"], target.get("name"))
    record(
        AuditAction.STORE_ASSIGN,
        ResourceType.STORE,
        resource_id=store_id,
        actor=current["user"],
        before={
            "sales_rep_id": store.get("sales_rep_id"),
            "sales_rep_name": store.get("sales_rep_name"),
        },
        after={
            "sales_rep_id": after["sales_rep_id"],
            "sales_rep_name": after["sales_rep_name"],
        },
        request=request,
    )
    return after


@router.patch("/{store_id}/credit-limit", response_model=Store)
async def propose_credit_limit(
    store_id: str,
    payload: CreditLimitPropose,
    request: Request,
    current=Depends(require_office),
):
    """Office proposes a new credit_limit for a store. The change is
    parked as `pending_credit_limit` and doesn't take effect until an
    admin approves it via POST /credit-limit/approve. Overwrites any
    prior pending proposal."""
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    after = StoreRepository.propose_credit_limit(store_id, payload.credit_limit)
    record(
        AuditAction.STORE_CREDIT_LIMIT_PROPOSE,
        ResourceType.STORE,
        resource_id=store_id,
        actor=current["user"],
        before={
            "credit_limit": store.get("credit_limit"),
            "pending_credit_limit": store.get("pending_credit_limit"),
        },
        after={
            "credit_limit": after.get("credit_limit"),
            "pending_credit_limit": after.get("pending_credit_limit"),
            "reason": payload.reason,
        },
        request=request,
    )
    return after


@router.post("/{store_id}/credit-limit/approve", response_model=Store)
async def approve_credit_limit(
    store_id: str,
    request: Request,
    current=Depends(require_admin),
):
    """Admin approves the pending credit-limit change. Applies the
    pending value as the new credit_limit and clears the pending state."""
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    if store.get("pending_credit_limit") is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No pending credit-limit change to approve.",
        )
    after = StoreRepository.approve_credit_limit(store_id)
    if after is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Pending credit-limit change cleared before approval could complete.",
        )
    record(
        AuditAction.STORE_CREDIT_LIMIT_APPROVE,
        ResourceType.STORE,
        resource_id=store_id,
        actor=current["user"],
        before={"credit_limit": store.get("credit_limit")},
        after={"credit_limit": after.get("credit_limit")},
        request=request,
    )
    return after


@router.post("/{store_id}/credit-limit/reject", response_model=Store)
async def reject_credit_limit(
    store_id: str,
    payload: CreditLimitReject,
    request: Request,
    current=Depends(require_admin),
):
    """Admin rejects the pending credit-limit change. credit_limit stays
    unchanged; pending_credit_limit is cleared with credit_change_status
    set to rejected."""
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    if store.get("pending_credit_limit") is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No pending credit-limit change to reject.",
        )
    after = StoreRepository.reject_credit_limit(store_id)
    record(
        AuditAction.STORE_CREDIT_LIMIT_REJECT,
        ResourceType.STORE,
        resource_id=store_id,
        actor=current["user"],
        before={"pending_credit_limit": store.get("pending_credit_limit")},
        after={"credit_limit": after.get("credit_limit"), "reason": payload.reason},
        request=request,
    )
    return after


@router.get(
    "/{store_id}/recommended-products",
    response_model=RecommendedProductsResponse,
)
async def recommended_products_for_store(
    store_id: str,
    limit: int = Query(10, ge=1, le=50),
    current=Depends(require_any_user),
):
    """Products this store should reorder next. Three-tier fallback:
      1. `store_history` — the store's own past orders (best signal).
      2. `district_fallback` — top products across other stores in the
         same district, when this store has no order history.
      3. `global_fallback` — site-wide top products, only if the district
         is also barren.

    Sales rep can only see recommendations for their assigned stores."""
    store = StoreRepository.by_id(store_id)
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    if not _visible(current["user"], store):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")

    source, items = analytics.recommended_products_for_store(
        store_id, district=store.get("district"), limit=limit,
    )
    # Hydrate each ranking snapshot into a full Product doc + score
    # metadata. Frontend can reuse the existing product card without
    # mapping.
    from repository.product_repo import ProductRepository
    recommended = []
    for rank, item in enumerate(items, start=1):
        product = ProductRepository.by_id(item["product_id"])
        if not product:
            continue
        recommended.append({
            "product": product,
            "recommendation": {
                "variant_id": item.get("variant_id"),
                "variant_label": item.get("variant_label"),
                "qty": int(item.get("qty") or 0),
                "revenue": float(item.get("revenue") or 0),
                "orders": int(item.get("orders") or 0),
                "last_ordered_at": item.get("last_ordered_at"),
                "source": source,
                "rank": rank,
            },
        })
    return {
        "store_id": store_id,
        "store_name": store.get("name"),
        "source": source,
        "recommended_products": recommended,
    }
