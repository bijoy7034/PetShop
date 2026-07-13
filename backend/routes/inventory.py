from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_any_user, require_office
from repository.inventory_repo import InventoryRepository
from schemas.inventory import (
    Inventory,
    InventoryListResponse,
    InventoryUpdate,
    StockAdjust,
)
from services.audit_service import record

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("", response_model=InventoryListResponse)
async def list_inventory(
    product_id: str | None = Query(None),
    low_stock: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(require_any_user),
):
    skip = (page - 1) * page_size
    items, total = InventoryRepository.list(
        product_id=product_id, low_stock=low_stock, skip=skip, limit=page_size
    )
    return InventoryListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/{inv_id}", response_model=Inventory)
async def get_inventory(inv_id: str, _=Depends(require_any_user)):
    inv = InventoryRepository.by_id(inv_id)
    if not inv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory row not found")
    return inv


@router.patch("/{inv_id}", response_model=Inventory)
async def update_inventory(
    inv_id: str,
    payload: InventoryUpdate,
    request: Request,
    current=Depends(require_office),
):
    before = InventoryRepository.by_id(inv_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory row not found")
    if payload.reorder_level is None:
        return before
    after = InventoryRepository.set_reorder_level(inv_id, payload.reorder_level)
    record(
        AuditAction.INVENTORY_REORDER_SET,
        ResourceType.INVENTORY,
        resource_id=inv_id,
        actor=current["user"],
        before={"reorder_level": before["reorder_level"]},
        after={"reorder_level": after["reorder_level"]},
        request=request,
    )
    return after


@router.post("/{inv_id}/adjust", response_model=Inventory)
async def adjust_inventory(
    inv_id: str,
    payload: StockAdjust,
    request: Request,
    current=Depends(require_office),
):
    """Manual on-hand adjustment. Same semantics as the legacy
    /products/{pid}/variants/{vid}/adjust-stock route (which now delegates
    here internally). Refuses if the resulting on-hand would drop below the
    current reserved_quantity."""
    if payload.delta == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Delta cannot be zero")
    before = InventoryRepository.by_id(inv_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory row not found")
    after = InventoryRepository.adjust_on_hand(before["variant_id"], payload.delta)
    if after is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Adjustment refused: would push on-hand below reserved quantity.",
        )
    record(
        AuditAction.INVENTORY_ADJUST,
        ResourceType.INVENTORY,
        resource_id=inv_id,
        actor=current["user"],
        before={"quantity_on_hand": before["quantity_on_hand"]},
        after={
            "quantity_on_hand": after["quantity_on_hand"],
            "delta": payload.delta,
            "reason": payload.reason,
        },
        request=request,
    )
    return after
