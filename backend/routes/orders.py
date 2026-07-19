from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from enums.order import ORDER_TRANSITIONS, OrderStatus
from enums.store import StoreStatus
from enums.user import Role
from middleware.auth import (
    require_admin,
    require_any_user,
    require_office,
    require_sales_rep,
)
from repository.order_repo import OrderRepository
from repository.store_repo import StoreRepository
from schemas.order import (
    Order,
    OrderAccept,
    OrderCancel,
    OrderCreate,
    OrderDelay,
    OrderListResponse,
    OrderReject,
    PaymentCreate,
)
from services.audit_service import record
from services.order_service import (
    apply_accept_adjustments,
    commit_inventory_for,
    price_order_lines,
    release_inventory_for,
    release_surplus_reservations,
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

    # Credit check — over-limit orders no longer reject; they go into
    # pending_admin_approval and don't consume credit until an admin
    # approves them.
    available = float(store.get("credit_limit", 0)) - float(store.get("credit_used", 0))
    over_credit = order_total > available
    initial_status = (
        OrderStatus.PENDING_ADMIN_APPROVAL.value if over_credit
        else OrderStatus.PLACED.value
    )

    # Reserve inventory regardless — the sales rep's intent should hold
    # the units even while admin decides. Rejection releases them.
    reserve_err = reserve_inventory_for(lines)
    if reserve_err:
        raise HTTPException(status.HTTP_409_CONFLICT, reserve_err)

    # Only bump credit_used when the order is going straight to 'placed'.
    # Pending orders don't count against the credit line yet.
    if not over_credit:
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
            status=initial_status,
            expected_delivery_date=payload.expected_delivery_date,
        )
    except Exception:
        if not over_credit:
            StoreRepository.adjust_credit_used(store["_id"], -order_total)
        release_inventory_for(lines)
        raise

    record(
        AuditAction.ORDER_PENDING_APPROVAL if over_credit else AuditAction.ORDER_PLACE,
        ResourceType.ORDER,
        resource_id=order["_id"],
        actor=user,
        after={
            "store_id": store["_id"],
            "total": order_total,
            "lines": len(lines),
            "status": initial_status,
            "over_credit": over_credit,
            "available_at_placement": available,
        },
        request=request,
    )
    return order


@router.post("/{order_id}/admin-approve", response_model=Order)
async def admin_approve_order(
    order_id: str,
    request: Request,
    current=Depends(require_admin),
):
    """Admin approves a pending_admin_approval order. The credit line is
    re-checked (another paid order could have consumed the room since the
    over-credit order was placed); if it still doesn't fit, returns 409
    and the admin can either reject the order or raise the store's
    credit_limit."""
    order = OrderRepository.by_id(order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order["status"] != OrderStatus.PENDING_ADMIN_APPROVAL.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Only pending_admin_approval orders can be approved. Current: '{order['status']}'.",
        )
    store = StoreRepository.by_id(order["store_id"])
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store no longer exists")
    available = float(store.get("credit_limit", 0)) - float(store.get("credit_used", 0))
    total = float(order.get("total") or 0)
    if total > available + 1e-6:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot approve — order total {total:.2f} still exceeds available "
            f"credit {available:.2f}. Raise the store's credit_limit or reject.",
        )
    hold = StoreRepository.adjust_credit_used(order["store_id"], total)
    if hold is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Credit hold failed at approval (concurrent write). Retry.",
        )
    after = OrderRepository.set_status(
        order_id, OrderStatus.PLACED.value, current["user"], note="Approved by admin."
    )
    record(
        AuditAction.ORDER_ADMIN_APPROVE,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={"status": order["status"]},
        after={"status": after["status"], "credit_held": total},
        request=request,
    )
    return after


@router.post("/{order_id}/admin-reject", response_model=Order)
async def admin_reject_order(
    order_id: str,
    payload: OrderReject,
    request: Request,
    current=Depends(require_admin),
):
    """Admin rejects a pending_admin_approval order. Inventory reservations
    are released; no credit line touched (never bumped for pending). The
    order moves to 'cancelled' with rejection_reason recorded."""
    order = OrderRepository.by_id(order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order["status"] != OrderStatus.PENDING_ADMIN_APPROVAL.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Only pending_admin_approval orders can be admin-rejected. Current: '{order['status']}'.",
        )
    release_inventory_for(order["lines"])
    after = OrderRepository.admin_reject(order_id, payload.reason, current["user"])
    record(
        AuditAction.ORDER_ADMIN_REJECT,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={"status": order["status"]},
        after={"status": after["status"], "rejection_reason": payload.reason},
        request=request,
    )
    return after


@router.post("/{order_id}/delay", response_model=Order)
async def delay_order(
    order_id: str,
    payload: OrderDelay,
    request: Request,
    current=Depends(require_office),
):
    """Mark an active order as delayed with a mandatory reason. Reachable
    from placed/accepted/packing/out_for_delivery. From delayed, the
    office can resume to any next legal state."""
    order = OrderRepository.by_id(order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    allowed = ORDER_TRANSITIONS.get(order["status"], ())
    if OrderStatus.DELAYED.value not in allowed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot mark a '{order['status']}' order as delayed.",
        )
    after = OrderRepository.mark_delayed(order_id, payload.reason, current["user"])
    record(
        AuditAction.ORDER_DELAY,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={"status": order["status"]},
        after={"status": after["status"], "delay_reason": payload.reason},
        request=request,
    )
    return after


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
    # Accept is legal from placed OR delayed (resume a paused order).
    if OrderStatus.ACCEPTED.value not in ORDER_TRANSITIONS.get(order["status"], ()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot accept an order in '{order['status']}' status.",
        )

    adjustments = payload.lines if payload else None
    new_lines, new_total, err = apply_accept_adjustments(order["lines"], adjustments)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    edited = adjustments is not None and any(
        (l.get("qty_accepted") or 0) != int(l["qty_ordered"]) for l in new_lines
    )
    old_total = float(order.get("total") or 0)

    if edited:
        # Release the surplus reservations back to inventory.
        release_surplus_reservations(new_lines)

        # Adjust the store's credit line: previously-held old_total was
        # based on qty_ordered; we're keeping only new_total. Excess is
        # released to the credit line.
        credit_delta = new_total - old_total
        if credit_delta != 0:
            StoreRepository.adjust_credit_used(order["store_id"], credit_delta)

        note = payload.note or "Quantities adjusted at acceptance."
        order = OrderRepository.update_lines(
            order_id, new_lines, new_total, current["user"], note=note
        )
        record(
            AuditAction.ORDER_LINES_EDIT,
            ResourceType.ORDER,
            resource_id=order_id,
            actor=current["user"],
            before={"total": old_total},
            after={
                "total": new_total,
                "lines": [_line_brief(l) for l in new_lines],
            },
            request=request,
        )
    else:
        order = OrderRepository.update_lines(
            order_id, new_lines, new_total, current["user"], note=None, log_edit=False
        )

    # Commit inventory at the final qty_accepted for each line. Each
    # commit writes a stock_history entry tagged with this order's code.
    err = commit_inventory_for(
        order["lines"], order_code=order.get("code"), actor=current["user"]
    )
    if err:
        raise HTTPException(status.HTTP_409_CONFLICT, err)

    after = OrderRepository.mark_accepted(order_id, current["user"])
    record(
        AuditAction.ORDER_ACCEPT,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={"status": order["status"]},
        after={
            "status": OrderStatus.ACCEPTED.value,
            "edited": bool(edited),
            "accepted_by_id": after.get("accepted_by_id"),
            "accepted_by_name": after.get("accepted_by_name"),
        },
        request=request,
    )
    return after


def _line_brief(line):
    return {
        "product_id": line.get("product_id"),
        "variant_id": line.get("variant_id"),
        "qty_ordered": line.get("qty_ordered"),
        "qty_accepted": line.get("qty_accepted"),
        "unit_price": line.get("unit_price"),
    }


@router.post("/{order_id}/deliver", response_model=Order)
async def deliver_order(
    order_id: str,
    request: Request,
    current=Depends(require_office),
):
    """Mark the order as delivered. Stamps delivered_at and computes
    payment_due_date from the snapshotted credit_period_days."""
    order = OrderRepository.by_id(order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    allowed = ORDER_TRANSITIONS.get(order["status"], ())
    if OrderStatus.DELIVERED.value not in allowed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot deliver an order in '{order['status']}' status.",
        )
    after = OrderRepository.mark_delivered(order_id, current["user"])
    record(
        AuditAction.ORDER_DELIVER,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={"status": order["status"]},
        after={
            "status": after["status"],
            "delivered_at": after.get("delivered_at"),
            "payment_due_date": after.get("payment_due_date"),
        },
        request=request,
    )
    return after


router.post("/{order_id}/pack", response_model=Order)(
    _transition_route(OrderStatus.PACKING.value, AuditAction.ORDER_PACK)
)
router.post("/{order_id}/dispatch", response_model=Order)(
    _transition_route(OrderStatus.OUT_FOR_DELIVERY.value, AuditAction.ORDER_DISPATCH)
)
