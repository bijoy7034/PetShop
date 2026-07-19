from repository.inventory_repo import InventoryRepository
from repository.product_repo import ProductRepository


def _effective_qty(line):
    """The qty that currently represents this line's stock claim.
    While placed → qty_ordered. Once accepted → qty_accepted."""
    if line.get("qty_accepted") is not None:
        return int(line["qty_accepted"])
    return int(line.get("qty_ordered") or 0)


def price_order_lines(line_payloads):
    """Look up each (product, variant) and price the line for placement.
    Rejects inactive product / variant and checks available stock covers
    the requested qty. Emits lines shaped for storage on the order.

    Returns (lines, total, error). On error, `lines` is empty.
    """
    lines = []
    total = 0.0
    for i, lp in enumerate(line_payloads, start=1):
        info = ProductRepository.get_variant(lp.product_id, lp.variant_id)
        if not info:
            return [], 0.0, f"Line {i}: product or variant not found."
        if not info.get("product_active", True):
            return [], 0.0, (
                f"Line {i}: '{info['product_name']}' is inactive and "
                f"cannot be added to new orders."
            )
        if not info.get("variant_active", True):
            return [], 0.0, (
                f"Line {i}: variant '{info.get('variant_label') or info['variant_code']}' "
                f"is inactive."
            )
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
        list_price = float(info["price"])
        discount_price = info.get("discount_price")
        # Effective per-unit price is the discount if set, otherwise list.
        effective_unit = float(discount_price) if discount_price is not None else list_price
        line_total = round(effective_unit * lp.qty, 2)
        total += line_total
        lines.append(
            {
                "product_id": info["product_id"],
                "product_code": info.get("product_code"),
                "product_name": info["product_name"],
                "variant_id": info["variant_id"],
                "variant_code": info.get("variant_code"),
                "variant_label": info.get("variant_label"),
                "qty_ordered": int(lp.qty),
                "qty_accepted": None,
                "unit_price": list_price,
                "discount_price": float(discount_price) if discount_price is not None else None,
                "line_total": line_total,
            }
        )
    return lines, round(total, 2), None


def reserve_inventory_for(lines):
    applied = []
    for i, line in enumerate(lines, start=1):
        qty = int(line["qty_ordered"])
        ok = InventoryRepository.reserve(line["variant_id"], qty)
        if ok is None:
            for a in applied:
                InventoryRepository.release(a["variant_id"], int(a["qty_ordered"]))
            return (
                f"Line {i}: available stock changed while placing the order — "
                f"cannot reserve {qty} × {line['product_name']}."
            )
        applied.append(line)
    return None


def release_inventory_for(lines):
    """Release the CURRENT reservation for every line (cancel/reject).
    Uses the effective qty — before acceptance that's qty_ordered."""
    for line in lines:
        qty = _effective_qty(line)
        if qty > 0:
            InventoryRepository.release(line["variant_id"], qty)


def apply_accept_adjustments(order_lines, adjustments):
    """Compute the new per-line qty_accepted from the accept-body
    adjustments and validate.

    Rules:
      - Every entry in `adjustments` must match an existing order line by
        (product_id, variant_id). No new lines can be added at accept.
      - Each qty_accepted must be in [0, qty_ordered]. Bumping past
        qty_ordered at accept is not supported.
      - Lines omitted from `adjustments` default to qty_accepted == qty_ordered.

    Returns (new_lines, new_total, error). Does NOT touch inventory or the
    order document.
    """
    def _finalize(ordered, accepted, line):
        unit = float(
            line.get("discount_price")
            if line.get("discount_price") is not None
            else line["unit_price"]
        )
        return round(unit * accepted, 2)

    if not adjustments:
        new_lines = []
        total = 0.0
        for l in order_lines:
            ordered = int(l["qty_ordered"])
            lt = _finalize(ordered, ordered, l)
            total += lt
            new_lines.append({**l, "qty_accepted": ordered, "line_total": lt})
        return new_lines, round(total, 2), None

    by_pair = {(a.product_id, a.variant_id): int(a.qty) for a in adjustments}
    if len(by_pair) != len(adjustments):
        return [], 0.0, (
            "Duplicate (product_id, variant_id) in the accept body — one entry per line."
        )

    matched = set()
    new_lines = []
    total = 0.0
    for l in order_lines:
        ordered = int(l["qty_ordered"])
        pair = (l["product_id"], l["variant_id"])
        if pair in by_pair:
            accepted = by_pair[pair]
            if accepted > ordered:
                return [], 0.0, (
                    f"'{l['product_name']}': qty_accepted={accepted} exceeds "
                    f"qty_ordered={ordered}. Accept can only reduce quantities."
                )
            matched.add(pair)
        else:
            accepted = ordered
        lt = _finalize(ordered, accepted, l)
        total += lt
        new_lines.append({**l, "qty_accepted": accepted, "line_total": lt})

    unknown = set(by_pair) - matched
    if unknown:
        p, v = next(iter(unknown))
        return [], 0.0, (
            f"Adjustment refers to a (product, variant) pair that's not on the "
            f"order: product_id={p}, variant_id={v}."
        )

    return new_lines, round(total, 2), None


def release_surplus_reservations(new_lines):
    """For each line, release (qty_ordered − qty_accepted) reserved units
    so they become available again. Called during accept."""
    for line in new_lines:
        surplus = int(line["qty_ordered"]) - int(line["qty_accepted"])
        if surplus > 0:
            InventoryRepository.release(line["variant_id"], surplus)


def commit_inventory_for(lines):
    """Order accepted — turn each reservation into a real consumption at
    qty_accepted (or qty_ordered if not yet set)."""
    applied = []
    for i, line in enumerate(lines, start=1):
        qty = _effective_qty(line)
        if qty <= 0:
            continue
        ok = InventoryRepository.commit(line["variant_id"], qty)
        if ok is None:
            for a in applied:
                aq = _effective_qty(a)
                InventoryRepository.adjust_on_hand(a["variant_id"], aq)
                InventoryRepository.reserve(a["variant_id"], aq)
            return (
                f"Line {i}: inventory changed since order was placed — "
                f"cannot commit {qty} × {line['product_name']}."
            )
        applied.append(line)
    return None
