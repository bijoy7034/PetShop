from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from enums.store import StoreStatus
from middleware.auth import require_any_user, require_office
from repository.store_repo import StoreRepository
from schemas.store import (
    Store,
    StoreApprove,
    StoreCreate,
    StoreListResponse,
    StoreReject,
    StoreUpdate,
)
from services.audit_service import record

router = APIRouter(prefix="/stores", tags=["stores"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


def _visible(user, store):
    """A sales rep can only see stores they own. Office / admin see all."""
    if _is_office(user):
        return True
    return store["owner_id"] == user["_id"]


@router.get("", response_model=StoreListResponse)
async def list_stores(
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current=Depends(require_any_user),
):
    skip = (page - 1) * page_size
    owner_id = None if _is_office(current["user"]) else current["user"]["_id"]
    items, total = StoreRepository.list(
        owner_id=owner_id,
        status=status_filter,
        search=search,
        skip=skip,
        limit=page_size,
    )
    return StoreListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{store_id}", response_model=Store)
async def get_store(store_id: str, current=Depends(require_any_user)):
    store = StoreRepository.by_id(store_id)
    if not store or not _visible(current["user"], store):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    return store


@router.post("", response_model=Store, status_code=status.HTTP_201_CREATED)
async def create_store(
    payload: StoreCreate,
    request: Request,
    current=Depends(require_any_user),
):
    user = current["user"]
    if user["role"] != "sales_rep":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only sales representatives can add stores.",
        )
    store = StoreRepository.insert(
        owner_id=user["_id"],
        owner_name=user.get("name"),
        name=payload.name,
        location=payload.location,
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
    # Sales reps can only edit their own store details. Office/admin can edit
    # any store's profile (but approval fields go through the dedicated
    # approve/reject endpoints).
    if not _is_office(user) and store["owner_id"] != user["_id"]:
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
        before={"name": store["name"], "owner_id": store["owner_id"]},
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
    if store["status"] == StoreStatus.APPROVED.value:
        # Re-approval == credit limit change. Still legal.
        pass
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
