from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_any_user, require_office
from repository.inventory_repo import InventoryRepository
from schemas.analytics import LowStockReport
from schemas.inventory import (
    Inventory,
    InventoryListResponse,
    InventorySummary,
    InventoryUpdate,
    StockAdjust,
)
from services import notification_service, waiting_orders_service
from services.audit_service import record

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("", response_model=InventoryListResponse)
async def list_inventory(
    product_id: str | None = Query(None),
    category_id: str | None = Query(
        None,
        description="Filter to variants of products in this category.",
    ),
    low_stock: bool = Query(False),
    search: str | None = Query(
        None,
        description="Case-insensitive substring across product_name / variant_label.",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(require_any_user),
):
    skip = (page - 1) * page_size
    items, total = InventoryRepository.list(
        product_id=product_id,
        category_id=category_id,
        low_stock=low_stock,
        search=search,
        skip=skip,
        limit=page_size,
    )
    return InventoryListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/summary", response_model=InventorySummary)
async def inventory_summary(_=Depends(require_any_user)):
    """Portfolio inventory snapshot: variant count, total valuation
    (Σ on_hand × unit_price where unit_price prefers discount_price),
    and three health-state buckets. optimal_stock_count = total −
    (out_of_stock + low_stock)."""
    return InventoryRepository.summary()


@router.get("/low-stock-report", response_model=LowStockReport)
async def low_stock_report(
    category_id: str | None = Query(None),
    search: str | None = Query(None),
    _=Depends(require_any_user),
):
    """Rich low-stock report used for the inventory CSV export.
    Includes both variants at/below reorder_level AND variants at
    zero on-hand. Each row is a joined inventory + product + variant
    view — no client-side joins required to render the sheet."""
    items = InventoryRepository.low_stock_items(
        category_id=category_id, search=search,
    )
    return {"total_items": len(items), "items": items}


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
    if payload.delta == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Delta cannot be zero")
    before = InventoryRepository.by_id(inv_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory row not found")
    after = InventoryRepository.adjust_on_hand(
        before["variant_id"],
        payload.delta,
        reason=payload.reason,
        actor=current["user"],
    )
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
    notification_service.check_stock_after_adjust(
        updated=after,
        product_name=after.get("product_name") or "",
        variant_label=after.get("variant_label"),
        variant_id=after["variant_id"],
        product_id=after.get("product_id"),
    )
    if payload.delta > 0:
        waiting_orders_service.promote_waiting_orders(
            trigger_variant_id=after["variant_id"],
        )
    return after
