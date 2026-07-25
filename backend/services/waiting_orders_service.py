"""Auto-promotion for `waiting_for_stock` orders.

Called from every stock-increasing code path (inventory adjust, product
adjust-stock) after the stock has landed. Scans waiting orders in FIFO
order, tries to reserve every line, and promotes the order to
`ready_to_submit` if the reservation succeeds.

The reservation is atomic (`InventoryRepository.reserve` guards on
quantity_on_hand - reserved_quantity), so a race between two waiting
orders wanting the same 10 units is safe: the first one wins and grabs
the stock, the second one's reserve returns None and the order stays
waiting.

Kept synchronous — this is a small scan, not a queue. If it grows
enough to matter, pop it into a background task.
"""
from enums.audit import AuditAction, ResourceType
from enums.order import OrderStatus
from helpers.datetime import now_utc
from repository.inventory_repo import InventoryRepository
from repository.order_repo import OrderRepository
from services import notification_service
from services.audit_service import record


def _lines_reservable(order):
    """Cheap pre-check: does every line have enough available stock
    right now? We still call reserve() atomically after, but skipping
    the reserve loop for obviously-blocked orders keeps the scan
    cheaper when stock arrives that only helps some orders."""
    for line in order.get("lines") or []:
        inv = InventoryRepository.by_variant_id(line["variant_id"])
        if not inv:
            return False
        if int(inv.get("available") or 0) < int(line["qty_ordered"]):
            return False
    return True


def _reserve_all_or_release(order):
    """Try to reserve every line. Rolls back partial reserves if any
    line fails. Returns True on success."""
    applied = []
    for line in order.get("lines") or []:
        ok = InventoryRepository.reserve(
            line["variant_id"], int(line["qty_ordered"])
        )
        if ok is None:
            for a in applied:
                InventoryRepository.release(
                    a["variant_id"], int(a["qty_ordered"])
                )
            return False
        applied.append(line)
    return True


def promote_waiting_orders(*, trigger_variant_id=None):
    """Scan every waiting_for_stock order in FIFO order. For each one
    whose lines can all be reserved right now, promote it to
    ready_to_submit and notify the rep.

    `trigger_variant_id` is a hint — we only actually skip an order if
    NONE of its lines match the hint AND all its lines are already
    reservable (that's an inconsistent state that shouldn't happen but
    we handle it just in case). Effectively the scan runs the full
    waiting queue; the hint is just for logging.

    Returns a list of dicts describing what happened, one entry per
    promoted order — used by the caller for audit/notification bundling.
    """
    promoted = []
    orders = OrderRepository.find_waiting_for_stock()
    for order in orders:
        if not _lines_reservable(order):
            continue
        # Atomic reservation. If another concurrent write ate the stock
        # between the pre-check and the reserve, this returns False and
        # we just leave the order waiting.
        if not _reserve_all_or_release(order):
            continue
        now = now_utc()
        after = OrderRepository.set_status(
            order["_id"],
            OrderStatus.READY_TO_SUBMIT.value,
            actor={"_id": "system", "name": "System"},
            note="Stock is now available — auto-promoted.",
            extra={"ready_to_submit_at": now},
        )
        record(
            AuditAction.ORDER_READY_TO_SUBMIT,
            ResourceType.ORDER,
            resource_id=order["_id"],
            actor={"_id": "system", "name": "System"},
            before={"status": order["status"]},
            after={
                "status": after["status"],
                "triggered_by_variant_id": trigger_variant_id,
            },
        )
        notification_service.notify_order_ready_to_submit(order=after)
        promoted.append({
            "order_id": order["_id"],
            "code": order.get("code"),
            "sales_rep_id": order.get("sales_rep_id"),
        })
    return promoted
