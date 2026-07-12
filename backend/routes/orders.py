from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from enums.order import ORDER_TRANSITIONS, OrderStatus
from enums.store import StoreStatus
from middleware.auth import require_any_user, require_office, require_sales_rep
from repository.order_repo import OrderRepository
from repository.store_repo import StoreRepository
from schemas.order import Order, OrderCancel, OrderCreate, OrderListResponse
from services.audit_service import record
from services.order_service import decrement_inventory_for, price_order_lines

router = APIRouter(prefix="/orders", tags=["orders"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


def _visible(user, order):
    if _is_office(user):
        return True
    return order["owner_id"] == user["_id"]


@router.get("", response_model=OrderListResponse)
async def list_orders(
    store_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current=Depends(require_any_user),
):
    user = current["user"]
    owner_id = None if _is_office(user) else user["_id"]
    skip = (page - 1) * page_size
    items, total = OrderRepository.list(
        owner_id=owner_id,
        store_id=store_id,
        status=status_filter,
        skip=skip,
        limit=page_size,
    )
    return OrderListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{order_id}", response_model=Order)
async def get_order(order_id: str, current=Depends(require_any_user)):
    order = OrderRepository.by_id(order_id)
    if not order or not _visible(current["user"], order):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


@router.post("", response_model=Order, status_code=status.HTTP_201_CREATED)
async def place_order(
    payload: OrderCreate,
    request: Request,
    current=Depends(require_sales_rep),
):
    user = current["user"]
    store = StoreRepository.by_id(payload.store_id)
    if not store or store["owner_id"] != user["_id"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Store not found or does not belong to you.",
        )
    if store["status"] != StoreStatus.APPROVED.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Store is not approved — cannot place orders yet.",
        )

    lines, order_total, err = price_order_lines(payload.lines)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

    available = float(store.get("credit_limit", 0)) - float(store.get("credit_used", 0))
    if order_total > available:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Order total {order_total:.2f} exceeds available credit "
            f"{available:.2f} (limit {store['credit_limit']:.2f}, "
            f"used {store['credit_used']:.2f}).",
        )

    # Hold the credit at placement. It's released on cancellation.
    hold = StoreRepository.adjust_credit_used(store["_id"], order_total)
    if hold is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Credit hold failed (concurrent order). Please retry.",
        )

    try:
        order = OrderRepository.insert(
            store=store,
            owner=user,
            lines=lines,
            total=order_total,
            notes=payload.notes,
        )
    except Exception:
        StoreRepository.adjust_credit_used(store["_id"], -order_total)
        raise

    record(
        AuditAction.ORDER_PLACE,
        ResourceType.ORDER,
        resource_id=order["_id"],
        actor=user,
        after={"store_id": store["_id"], "total": order_total, "lines": len(lines)},
        request=request,
    )
    return order


@router.post("/{order_id}/cancel", response_model=Order)
async def cancel_order(
    order_id: str,
    payload: OrderCancel,
    request: Request,
    current=Depends(require_any_user),
):
    order = OrderRepository.by_id(order_id)
    if not order or not _visible(current["user"], order):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    user = current["user"]
    if order["status"] != OrderStatus.PLACED.value:
        # Once office has accepted, cancellation is not allowed via this
        # endpoint. That matches the spec — cancel is only "just before
        # accepting". Office can add a separate return/refund flow later.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only orders in 'placed' status can be cancelled.",
        )
    # Sales rep can cancel only their own; office can cancel any placed order.
    if not _is_office(user) and order["owner_id"] != user["_id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    after = OrderRepository.cancel(order_id, payload.reason, user)
    StoreRepository.adjust_credit_used(order["store_id"], -order["total"])
    record(
        AuditAction.ORDER_CANCEL,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=user,
        before={"status": order["status"]},
        after={"status": after["status"], "reason": payload.reason},
        request=request,
    )
    return after


def _transition_route(target_status, audit_action):
    async def _handler(
        order_id: str,
        request: Request,
        current=Depends(require_office),
    ):
        order = OrderRepository.by_id(order_id)
        if not order:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        allowed = ORDER_TRANSITIONS.get(order["status"], ())
        if target_status not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot go from '{order['status']}' to '{target_status}'.",
            )

        if target_status == OrderStatus.ACCEPTED.value:
            err = decrement_inventory_for(order["lines"])
            if err:
                raise HTTPException(status.HTTP_409_CONFLICT, err)

        after = OrderRepository.set_status(order_id, target_status, current["user"])
        record(
            audit_action,
            ResourceType.ORDER,
            resource_id=order_id,
            actor=current["user"],
            before={"status": order["status"]},
            after={"status": target_status},
            request=request,
        )
        return after

    return _handler


router.post("/{order_id}/accept", response_model=Order)(
    _transition_route(OrderStatus.ACCEPTED.value, AuditAction.ORDER_ACCEPT)
)
router.post("/{order_id}/pack", response_model=Order)(
    _transition_route(OrderStatus.PACKING.value, AuditAction.ORDER_PACK)
)
router.post("/{order_id}/dispatch", response_model=Order)(
    _transition_route(OrderStatus.OUT_FOR_DELIVERY.value, AuditAction.ORDER_DISPATCH)
)
router.post("/{order_id}/deliver", response_model=Order)(
    _transition_route(OrderStatus.DELIVERED.value, AuditAction.ORDER_DELIVER)
)
