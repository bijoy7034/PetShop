from repository.inventory_repo import InventoryRepository
from repository.product_repo import ProductRepository


def price_order_lines(line_payloads):
    """Look up each (product, variant), compute unit price, check that
    available stock (on_hand - reserved) covers the requested qty.

    Returns (lines, total, error). On error, `lines` is empty and `error`
    is the human-facing message describing what went wrong.
    """
    lines = []
    total = 0.0
    for i, lp in enumerate(line_payloads, start=1):
        info = ProductRepository.get_variant(lp.product_id, lp.variant_id)
        if not info:
            return [], 0.0, f"Line {i}: product or variant not found."
        inv = InventoryRepository.by_variant_id(info["variant_id"])
        if not inv:
            return [], 0.0, (
                f"Line {i}: no inventory record for "
                f"'{info['product_name']}'. Ask office to seed stock first."
            )
        if inv["available"] < lp.qty:
            return [], 0.0, (
                f"Line {i}: only {inv['available']} available for "
                f"'{info['product_name']}' ({info.get('variant_label') or 'default'}), "
                f"requested {lp.qty}."
            )
        unit = info.get("discount_price") if info.get("discount_price") is not None else info["price"]
        line_total = round(float(unit) * lp.qty, 2)
        total += line_total
        lines.append(
            {
                "product_id": info["product_id"],
                "product_name": info["product_name"],
                "variant_id": info["variant_id"],
                "variant_label": info.get("variant_label"),
                "qty": lp.qty,
                "unit_price": float(unit),
                "line_total": line_total,
            }
        )
    return lines, round(total, 2), None


def reserve_inventory_for(lines):
    """Reserve stock for every line atomically. If any line's reserve
    fails, releases the ones already reserved and returns the failing
    line's error string."""
    applied = []
    for i, line in enumerate(lines, start=1):
        ok = InventoryRepository.reserve(line["variant_id"], line["qty"])
        if ok is None:
            for a in applied:
                InventoryRepository.release(a["variant_id"], a["qty"])
            return (
                f"Line {i}: available stock changed while placing the order — "
                f"cannot reserve {line['qty']} × {line['product_name']}."
            )
        applied.append(line)
    return None


def release_inventory_for(lines):
    """Release reservations for every line (order cancelled). Best-effort:
    logs but does not raise on individual failures — cancel should not
    fail because a reservation somehow already went to zero."""
    for line in lines:
        InventoryRepository.release(line["variant_id"], line["qty"])


def commit_inventory_for(lines):
    """Order accepted — commit each reservation into a real consumption.
    Rolls back the ones already committed on partial failure."""
    applied = []
    for i, line in enumerate(lines, start=1):
        ok = InventoryRepository.commit(line["variant_id"], line["qty"])
        if ok is None:
            # Roll back: put the units back on hand AND back into reserved
            # so the order stays fulfillable if the caller retries.
            for a in applied:
                InventoryRepository.adjust_on_hand(a["variant_id"], a["qty"])
                InventoryRepository.reserve(a["variant_id"], a["qty"])
            return (
                f"Line {i}: inventory changed since order was placed — "
                f"cannot commit {line['qty']} × {line['product_name']}."
            )
        applied.append(line)
    return None
