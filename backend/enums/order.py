from enum import StrEnum


class OrderStatus(StrEnum):
    PLACED = "placed"
    ACCEPTED = "accepted"
    PACKING = "packing"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


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
