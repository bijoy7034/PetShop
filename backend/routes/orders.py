from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from enums.order import ORDER_TRANSITIONS, OrderStatus
from enums.store import StoreStatus
from middleware.auth import require_any_user, require_office, require_sales_rep
from repository.order_repo import OrderRepository
from repository.store_repo import StoreRepository
from schemas.order import (
    Order,
    OrderAccept,
    OrderCancel,
    OrderCreate,
    OrderListResponse,
    PaymentCreate,
)
from services.audit_service import record
from services.order_service import (
    apply_reservation_delta,
    commit_inventory_for,
    line_deltas,
    price_order_lines,
    release_inventory_for,
    reprice_lines,
    reserve_inventory_for,
)

router = APIRouter(prefix="/orders", tags=["orders"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


def _visible(user, order):
    if _is_office(user):
        return True
    return order.get("sales_rep_id") == user["_id"]


@router.get("", response_model=OrderListResponse)
async def list_orders(
    store_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    payment_status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current=Depends(require_any_user),
):
    user = current["user"]
    sales_rep_id = None if _is_office(user) else user["_id"]
    skip = (page - 1) * page_size
    items, total = OrderRepository.list(
        sales_rep_id=sales_rep_id,
        store_id=store_id,
        status=status_filter,
        payment_status=payment_status,
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
    if not store or store.get("sales_rep_id") != user["_id"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Store not found or is not assigned to you.",
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

    # Reserve inventory FIRST — if any line can't be reserved, no state has
    # to be rolled back. Then hold credit. Then insert the order.
    reserve_err = reserve_inventory_for(lines)
    if reserve_err:
        raise HTTPException(status.HTTP_409_CONFLICT, reserve_err)

    hold = StoreRepository.adjust_credit_used(store["_id"], order_total)
    if hold is None:
        release_inventory_for(lines)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Credit hold failed (concurrent order). Please retry.",
        )

    try:
        order = OrderRepository.insert(
            store=store,
            sales_rep=user,
            lines=lines,
            total=order_total,
            notes=payload.notes,
        )
    except Exception:
        StoreRepository.adjust_credit_used(store["_id"], -order_total)
        release_inventory_for(lines)
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
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only orders in 'placed' status can be cancelled.",
        )
    if not _is_office(user) and order.get("sales_rep_id") != user["_id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    after = OrderRepository.cancel(order_id, payload.reason, user)
    # Release inventory reservations and credit hold.
    release_inventory_for(order["lines"])
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

        # Accept turns each reservation into a real consumption.
        if target_status == OrderStatus.ACCEPTED.value:
            err = commit_inventory_for(order["lines"])
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


@router.post("/{order_id}/payment", response_model=Order)
async def record_payment(
    order_id: str,
    payload: PaymentCreate,
    request: Request,
    current=Depends(require_office),
):
    """Record a payment against an order. Independent of the delivery
    status — an order can be paid before, during, or after fulfillment.

    Server derives payment_status from the running total: 0 -> pending,
    partial -> partially_paid, full -> paid. Overpayment is refused.
    Each payment also decrements store.credit_used by the same amount so
    credit is released as receivables are settled.
    """
    order = OrderRepository.by_id(order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order["status"] == OrderStatus.CANCELLED.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot record a payment against a cancelled order.",
        )
    outstanding = float(order.get("outstanding") or 0)
    if payload.amount > outstanding + 1e-6:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Payment {payload.amount:.2f} exceeds outstanding balance "
            f"{outstanding:.2f}.",
        )

    after = OrderRepository.record_payment(
        order_id, payload.amount, payload.method, payload.notes, current["user"]
    )
    if after is None:
        # Overpayment guard or concurrent write.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Payment could not be recorded (concurrent write or overpayment). Retry.",
        )

    # Release the paid amount from the store's credit line (accounts
    # receivable settled). Best-effort — the payment is already durable,
    # a credit-release failure is worth surfacing but not worth reversing.
    StoreRepository.adjust_credit_used(order["store_id"], -payload.amount)

    record(
        AuditAction.ORDER_PAYMENT_RECORDED,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={
            "amount_paid": order.get("amount_paid"),
            "payment_status": order.get("payment_status"),
        },
        after={
            "amount_paid": after["amount_paid"],
            "payment_status": after["payment_status"],
            "payment_amount": payload.amount,
            "method": payload.method,
        },
        request=request,
    )
    return after


@router.post("/{order_id}/accept", response_model=Order)
async def accept_order(
    order_id: str,
    request: Request,
    payload: OrderAccept | None = None,
    current=Depends(require_office),
):
    """Accept a placed order and commit inventory.

    Body is optional. If `lines` is supplied, the order is repriced, the
    credit line is re-checked, inventory reservations are swapped (delta),
    and the order document is updated with the new lines and total —
    then the accept proceeds to commit the (edited) reservations into
    real stock consumption.
    """
    order = OrderRepository.by_id(order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order["status"] != OrderStatus.PLACED.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot accept an order in '{order['status']}' status.",
        )

    edited = payload is not None and payload.lines
    if edited:
        # 1. Reprice new lines (no stock check here — the reservation
        # delta below does its own atomic check).
        new_lines, new_total, err = reprice_lines(payload.lines)
        if err:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

        # 2. Credit check — the new total must fit in the credit line
        # accounting for the current order's already-held credit.
        store = StoreRepository.by_id(order["store_id"])
        if not store:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Store attached to order no longer exists.",
            )
        old_total = float(order.get("total") or 0)
        current_used = float(store.get("credit_used") or 0)
        # Available room = limit - (credit currently used by everyone
        # OTHER than this order). Add old_total back because we're
        # about to release it.
        headroom = float(store.get("credit_limit", 0)) - (current_used - old_total)
        if new_total > headroom + 1e-6:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Edited order total {new_total:.2f} exceeds available credit "
                f"{headroom:.2f} for this store.",
            )

        # 3. Apply the reservation delta. Positive reserves are attempted
        # first with rollback on failure — inventory is unchanged if we
        # fail here.
        deltas = line_deltas(order["lines"], new_lines)
        if deltas:
            err = apply_reservation_delta(deltas)
            if err:
                raise HTTPException(status.HTTP_409_CONFLICT, err)

        # 4. Adjust the store's credit_used by (new - old).
        credit_delta = new_total - old_total
        if credit_delta != 0:
            StoreRepository.adjust_credit_used(store["_id"], credit_delta)

        # 5. Persist the edit on the order document.
        old_lines_snapshot = list(order["lines"])
        note = payload.note or "Lines edited by office at acceptance."
        order = OrderRepository.update_lines(
            order_id, new_lines, new_total, current["user"], note=note
        )
        record(
            AuditAction.ORDER_LINES_EDIT,
            ResourceType.ORDER,
            resource_id=order_id,
            actor=current["user"],
            before={
                "lines": [_line_brief(l) for l in old_lines_snapshot],
                "total": old_total,
            },
            after={
                "lines": [_line_brief(l) for l in new_lines],
                "total": new_total,
            },
            request=request,
        )

    # 6. Commit inventory: turn reservations into consumptions.
    err = commit_inventory_for(order["lines"])
    if err:
        raise HTTPException(status.HTTP_409_CONFLICT, err)

    after = OrderRepository.set_status(order_id, OrderStatus.ACCEPTED.value, current["user"])
    record(
        AuditAction.ORDER_ACCEPT,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={"status": order["status"]},
        after={"status": OrderStatus.ACCEPTED.value, "edited": bool(edited)},
        request=request,
    )
    return after


def _line_brief(line):
    return {
        "product_id": line.get("product_id"),
        "variant_id": line.get("variant_id"),
        "qty": line.get("qty"),
        "unit_price": line.get("unit_price"),
    }


router.post("/{order_id}/pack", response_model=Order)(
    _transition_route(OrderStatus.PACKING.value, AuditAction.ORDER_PACK)
)
router.post("/{order_id}/dispatch", response_model=Order)(
    _transition_route(OrderStatus.OUT_FOR_DELIVERY.value, AuditAction.ORDER_DISPATCH)
)
router.post("/{order_id}/deliver", response_model=Order)(
    _transition_route(OrderStatus.DELIVERED.value, AuditAction.ORDER_DELIVER)
)
