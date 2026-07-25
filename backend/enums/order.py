from enum import StrEnum


class OrderStatus(StrEnum):
    # Rep placed the order but one or more lines had insufficient stock.
    # No inventory reservation, no credit hold. When stock arrives,
    # promote_waiting_for_stock lifts the order to READY_TO_SUBMIT.
    WAITING_FOR_STOCK = "waiting_for_stock"
    # Stock is now available and reserved. Rep still needs to actively
    # submit the order — auto-promotion won't push into the fulfilment
    # pipeline in case the rep or the store changed their mind.
    READY_TO_SUBMIT = "ready_to_submit"
    PENDING_ADMIN_APPROVAL = "pending_admin_approval"
    PLACED = "placed"
    ACCEPTED = "accepted"
    PACKING = "packing"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    DELAYED = "delayed"


class PaymentStatus(StrEnum):
    """Independent from OrderStatus — a delivered order can still be
    unpaid, and an order can be paid before it ships."""
    PENDING = "pending"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"


def payment_status_from(total, amount_paid):
    """Derive the payment status from the running totals so it can never
    disagree with the money on file."""
    total = float(total or 0)
    paid = float(amount_paid or 0)
    if paid <= 0:
        return PaymentStatus.PENDING.value
    if paid >= total:
        return PaymentStatus.PAID.value
    return PaymentStatus.PARTIALLY_PAID.value


# Legal transitions used by the office-driven state machine. Sales rep
# cancellation is a separate check (only allowed from PLACED) enforced in
# the route, not this table. DELAYED is an overlay reachable from any
# active status; from DELAYED the order can resume to any next state.
_ACTIVE = (
    OrderStatus.PLACED.value,
    OrderStatus.ACCEPTED.value,
    OrderStatus.PACKING.value,
    OrderStatus.OUT_FOR_DELIVERY.value,
)

ORDER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    OrderStatus.WAITING_FOR_STOCK.value: (
        OrderStatus.READY_TO_SUBMIT.value,
        OrderStatus.CANCELLED.value,
    ),
    OrderStatus.READY_TO_SUBMIT.value: (
        OrderStatus.PLACED.value,
        OrderStatus.PENDING_ADMIN_APPROVAL.value,  # over-credit path
        OrderStatus.WAITING_FOR_STOCK.value,       # stock re-lost before submit
        OrderStatus.CANCELLED.value,
    ),
    OrderStatus.PENDING_ADMIN_APPROVAL.value: (
        OrderStatus.PLACED.value,
        OrderStatus.CANCELLED.value,
    ),
    OrderStatus.PLACED.value: (
        OrderStatus.ACCEPTED.value,
        OrderStatus.DELAYED.value,
    ),
    OrderStatus.ACCEPTED.value: (
        OrderStatus.PACKING.value,
        OrderStatus.DELAYED.value,
    ),
    OrderStatus.PACKING.value: (
        OrderStatus.OUT_FOR_DELIVERY.value,
        OrderStatus.DELAYED.value,
    ),
    OrderStatus.OUT_FOR_DELIVERY.value: (
        OrderStatus.DELIVERED.value,
        OrderStatus.DELAYED.value,
    ),
    OrderStatus.DELAYED.value: (
        OrderStatus.ACCEPTED.value,
        OrderStatus.PACKING.value,
        OrderStatus.OUT_FOR_DELIVERY.value,
        OrderStatus.DELIVERED.value,
    ),
}

# Statuses that still count against a store's credit line.
# pending_admin_approval doesn't count (admin hasn't approved the exposure yet).
# cancelled releases credit.
OPEN_STATUSES: frozenset[str] = frozenset(
    (
        OrderStatus.PLACED.value,
        OrderStatus.ACCEPTED.value,
        OrderStatus.PACKING.value,
        OrderStatus.OUT_FOR_DELIVERY.value,
        OrderStatus.DELIVERED.value,
        OrderStatus.DELAYED.value,
    )
)
