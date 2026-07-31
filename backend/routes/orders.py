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
    OrderStats,
    PaymentCreate,
    PlaceOrderResponse,
)
from services import notification_service, waiting_orders_service
from services.audit_service import record
from services.order_service import (
    _totals_for,
    apply_accept_adjustments,
    commit_inventory_for,
    price_order_lines,
    release_inventory_for,
    release_surplus_reservations,
    reserve_inventory_for,
    split_lines_by_stock,
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
    sales_rep_id: str | None = Query(
        None,
        description="Office/admin only; sales_rep is force-scoped to their own orders.",
    ),
    status_filter: str | None = Query(None, alias="status"),
    payment_status: str | None = Query(None),
    over_credit_approved: bool | None = Query(
        None,
        description="Filter for orders approved despite exceeding the store's "
                    "credit limit. true = show exposure list; false = exclude.",
    ),
    search: str | None = Query(
        None,
        description="Case-insensitive substring across order code / store name / store code / rep name.",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current=Depends(require_any_user),
):
    user = current["user"]
    # Sales rep force-scoped to themselves; office/admin can pass any rep.
    effective_rep = sales_rep_id if _is_office(user) else user["_id"]
    skip = (page - 1) * page_size
    items, total = OrderRepository.list(
        sales_rep_id=effective_rep,
        store_id=store_id,
        status=status_filter,
        payment_status=payment_status,
        over_credit_approved=over_credit_approved,
        search=search,
        skip=skip,
        limit=page_size,
    )
    return OrderListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=OrderStats)
async def order_stats(
    rep_id: str | None = Query(
        None,
        description="Office/admin can pass any rep id; sales_rep is force-scoped to their own.",
    ),
    store_id: str | None = Query(None),
    current=Depends(require_any_user),
):
    """Dashboard summary — total orders, total volume, and counts
    grouped by status and payment_status. Every OrderStatus /
    PaymentStatus key is present (zero-filled) so the frontend can
    render its full grid without missing-key guards."""
    user = current["user"]
    # Sales rep is force-scoped to themselves regardless of the query.
    effective_rep = rep_id if _is_office(user) else user["_id"]
    return OrderRepository.stats(sales_rep_id=effective_rep, store_id=store_id)


@router.get("/{order_id}", response_model=Order)
async def get_order(order_id: str, current=Depends(require_any_user)):
    order = OrderRepository.by_id(order_id)
    if not order or not _visible(current["user"], order):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


def _place_in_stock_order(*, store, user, lines, total, notes,
                          expected_delivery_date, sibling_id, request):
    """Extracted from the old single-order path so the split flow can
    call it after peeling off the backordered lines. Handles credit
    check, inventory reservation, DB insert, audit, notification."""
    available = float(store.get("credit_limit", 0)) - float(store.get("credit_used", 0))
    over_credit = total > available
    initial_status = (
        OrderStatus.PENDING_ADMIN_APPROVAL.value if over_credit
        else OrderStatus.PLACED.value
    )
    reserve_err = reserve_inventory_for(lines)
    if reserve_err:
        raise HTTPException(status.HTTP_409_CONFLICT, reserve_err)
    if not over_credit:
        hold = StoreRepository.adjust_credit_used(store["_id"], total)
        if hold is None:
            release_inventory_for(lines)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Credit hold failed (concurrent order). Please retry.",
            )
    try:
        order = OrderRepository.insert(
            store=store, sales_rep=user, lines=lines, total=total,
            notes=notes, status=initial_status,
            expected_delivery_date=expected_delivery_date,
            sibling_order_id=sibling_id,
        )
    except Exception:
        if not over_credit:
            StoreRepository.adjust_credit_used(store["_id"], -total)
        release_inventory_for(lines)
        raise

    record(
        AuditAction.ORDER_PENDING_APPROVAL if over_credit else AuditAction.ORDER_PLACE,
        ResourceType.ORDER,
        resource_id=order["_id"],
        actor=user,
        after={
            "store_id": store["_id"],
            "total": total,
            "lines": len(lines),
            "status": initial_status,
            "over_credit": over_credit,
            "available_at_placement": available,
            "sibling_order_id": sibling_id,
        },
        request=request,
    )
    notification_service.notify_order_placed(order=order)
    return order


def _place_waiting_order(*, store, user, lines, total, notes,
                        expected_delivery_date, sibling_id, request):
    """Waiting orders skip inventory reservation and credit hold — the
    stock isn't there yet, and credit will be checked when the order is
    submitted after auto-promotion."""
    order = OrderRepository.insert(
        store=store, sales_rep=user, lines=lines, total=total,
        notes=notes, status=OrderStatus.WAITING_FOR_STOCK.value,
        expected_delivery_date=expected_delivery_date,
        sibling_order_id=sibling_id,
    )
    record(
        AuditAction.ORDER_WAITING_FOR_STOCK,
        ResourceType.ORDER,
        resource_id=order["_id"],
        actor=user,
        after={
            "store_id": store["_id"],
            "total": total,
            "lines": len(lines),
            "sibling_order_id": sibling_id,
        },
        request=request,
    )
    notification_service.notify_order_waiting_for_stock(order=order)
    return order


@router.post("", response_model=PlaceOrderResponse, status_code=status.HTTP_201_CREATED)
async def place_order(
    payload: OrderCreate,
    request: Request,
    current=Depends(require_sales_rep),
):
    """Place an order. Out-of-stock lines are accepted and split off
    into a companion `waiting_for_stock` order — auto-promoted to
    `ready_to_submit` when stock arrives.

    Response shape:
      - all lines in stock → { primary_order, waiting_order: null, split: false }
      - all lines out of stock → { primary_order: <the waiting order>, waiting_order: null, split: false }
      - mixed → { primary_order, waiting_order, split: true }
    """
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

    lines, _order_total, err = price_order_lines(payload.lines)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

    in_stock, out_of_stock = split_lines_by_stock(lines)
    in_stock_total = _totals_for(in_stock)
    out_of_stock_total = _totals_for(out_of_stock)

    # --- Case 1: everything in stock (existing behaviour) ---
    if not out_of_stock:
        order = _place_in_stock_order(
            store=store, user=user, lines=in_stock, total=in_stock_total,
            notes=payload.notes,
            expected_delivery_date=payload.expected_delivery_date,
            sibling_id=None, request=request,
        )
        return PlaceOrderResponse(primary_order=order, waiting_order=None, split=False)

    # --- Case 2: everything out of stock ---
    if not in_stock:
        order = _place_waiting_order(
            store=store, user=user, lines=out_of_stock, total=out_of_stock_total,
            notes=payload.notes,
            expected_delivery_date=payload.expected_delivery_date,
            sibling_id=None, request=request,
        )
        return PlaceOrderResponse(primary_order=order, waiting_order=None, split=False)

    # --- Case 3: mixed → split ---
    primary = _place_in_stock_order(
        store=store, user=user, lines=in_stock, total=in_stock_total,
        notes=payload.notes,
        expected_delivery_date=payload.expected_delivery_date,
        sibling_id=None, request=request,
    )
    waiting = _place_waiting_order(
        store=store, user=user, lines=out_of_stock, total=out_of_stock_total,
        notes=payload.notes,
        expected_delivery_date=payload.expected_delivery_date,
        sibling_id=primary["_id"], request=request,
    )
    # Cross-link the primary now that we have the waiting id.
    primary = OrderRepository.set_sibling(primary["_id"], waiting["_id"])
    record(
        AuditAction.ORDER_SPLIT_ON_STOCK,
        ResourceType.ORDER,
        resource_id=primary["_id"],
        actor=user,
        after={
            "primary_order_id": primary["_id"],
            "waiting_order_id": waiting["_id"],
            "in_stock_lines": len(in_stock),
            "out_of_stock_lines": len(out_of_stock),
        },
        request=request,
    )
    return PlaceOrderResponse(primary_order=primary, waiting_order=waiting, split=True)


@router.post("/{order_id}/admin-approve", response_model=Order)
async def admin_approve_order(
    order_id: str,
    request: Request,
    current=Depends(require_admin),
):
    """Admin approves a pending_admin_approval order. Approval overrides
    the credit limit by design — that's the whole point of the workflow.
    credit_used is still bumped by the order total so the ledger stays
    honest; the store's `available_credit` may go negative until they
    pay down. Audit captures both the pre-bump exposure and the post-
    bump number so the override is visible."""
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
    available_before = float(store.get("credit_limit", 0)) - float(store.get("credit_used", 0))
    total = float(order.get("total") or 0)
    over_limit_by = max(0.0, total - available_before)

    hold = StoreRepository.adjust_credit_used(order["store_id"], total)
    if hold is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Credit hold failed at approval (concurrent write). Retry.",
        )

    from helpers.datetime import now_utc
    extra = None
    if over_limit_by > 0:
        # Stamp the order so the flag surfaces in every GET /orders read,
        # not just the audit log.
        extra = {
            "over_credit_approved": True,
            "over_credit_amount": over_limit_by,
            "over_credit_approved_at": now_utc(),
            "over_credit_approved_by_id": current["user"]["_id"],
            "over_credit_approved_by_name": current["user"].get("name"),
        }

    after = OrderRepository.set_status(
        order_id, OrderStatus.PLACED.value, current["user"],
        note=(
            "Approved by admin (OVER CREDIT — exceeded available "
            f"by {over_limit_by:.2f})."
            if over_limit_by > 0 else "Approved by admin."
        ),
        extra=extra,
    )
    record(
        AuditAction.ORDER_ADMIN_APPROVE,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={
            "status": order["status"],
            "available_credit_before": available_before,
        },
        after={
            "status": after["status"],
            "credit_held": total,
            "over_credit_approved": over_limit_by > 0,
            "over_credit_amount": over_limit_by,
        },
        request=request,
    )
    notification_service.notify_order_status(
        order=after, prev_status=order["status"], actor=current["user"],
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
    notification_service.notify_order_status(
        order=after, prev_status=order["status"], actor=current["user"],
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
        notification_service.notify_order_status(
            order=after, prev_status=order["status"], actor=current["user"],
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
    notification_service.notify_payment_collected(
        order=after, amount=payload.amount, actor=current["user"],
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
    notification_service.notify_order_status(
        order=after, prev_status=order["status"], actor=current["user"],
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
    notification_service.notify_order_status(
        order=after, prev_status=order["status"], actor=current["user"],
    )
    return after


router.post("/{order_id}/pack", response_model=Order)(
    _transition_route(OrderStatus.PACKING.value, AuditAction.ORDER_PACK)
)
router.post("/{order_id}/dispatch", response_model=Order)(
    _transition_route(OrderStatus.OUT_FOR_DELIVERY.value, AuditAction.ORDER_DISPATCH)
)


@router.post("/{order_id}/submit", response_model=Order)
async def submit_ready_order(
    order_id: str,
    request: Request,
    current=Depends(require_sales_rep),
):
    """Rep confirms a ready_to_submit order, pushing it into the
    fulfilment pipeline. Inventory is ALREADY reserved (promotion
    happened when stock arrived), so this endpoint just runs the credit
    check and transitions to placed (or pending_admin_approval if
    over-credit)."""
    order = OrderRepository.by_id(order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.get("sales_rep_id") != current["user"]["_id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order["status"] != OrderStatus.READY_TO_SUBMIT.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Only ready_to_submit orders can be submitted. Current: '{order['status']}'.",
        )

    store = StoreRepository.by_id(order["store_id"])
    if not store:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store no longer exists")
    order_total = float(order.get("total") or 0)
    available = float(store.get("credit_limit", 0)) - float(store.get("credit_used", 0))
    over_credit = order_total > available
    target_status = (
        OrderStatus.PENDING_ADMIN_APPROVAL.value if over_credit
        else OrderStatus.PLACED.value
    )
    if not over_credit:
        hold = StoreRepository.adjust_credit_used(store["_id"], order_total)
        if hold is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Credit hold failed (concurrent order). Please retry.",
            )

    after = OrderRepository.set_status(
        order_id, target_status, current["user"],
        note="Submitted by rep after stock became available.",
    )
    record(
        AuditAction.ORDER_SUBMIT_FROM_READY,
        ResourceType.ORDER,
        resource_id=order_id,
        actor=current["user"],
        before={"status": order["status"]},
        after={"status": target_status, "over_credit": over_credit},
        request=request,
    )
    if over_credit:
        # Notify admin like a fresh over-credit order would.
        notification_service.notify_order_placed(order=after)
    else:
        notification_service.notify_order_placed(order=after)
    return after
