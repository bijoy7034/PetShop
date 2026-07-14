from enum import StrEnum


class OrderStatus(StrEnum):
    PLACED = "placed"
    ACCEPTED = "accepted"
    PACKING = "packing"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


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
# the route, not this table.
ORDER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    OrderStatus.PLACED.value: (OrderStatus.ACCEPTED.value,),
    OrderStatus.ACCEPTED.value: (OrderStatus.PACKING.value,),
    OrderStatus.PACKING.value: (OrderStatus.OUT_FOR_DELIVERY.value,),
    OrderStatus.OUT_FOR_DELIVERY.value: (OrderStatus.DELIVERED.value,),
}

# Statuses that still count against a store's credit line. Cancelled orders
# release credit; delivered orders keep counting until manual write-off (not
# modelled in this release).
OPEN_STATUSES: frozenset[str] = frozenset(
    s.value
    for s in (
        OrderStatus.PLACED,
        OrderStatus.ACCEPTED,
        OrderStatus.PACKING,
        OrderStatus.OUT_FOR_DELIVERY,
        OrderStatus.DELIVERED,
    )
)
