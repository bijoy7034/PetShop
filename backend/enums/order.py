from enum import StrEnum


class OrderStatus(StrEnum):
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
