from repository.product_repo import ProductRepository


def _variant_label(v):
    parts = [p for p in (v.get("size"), v.get("weight"), v.get("color")) if p]
    if v.get("sku"):
        parts.append(f"SKU {v['sku']}")
    return " / ".join(parts) if parts else None


def price_order_lines(line_payloads):
    """Look up each (product, variant), compute unit price and line total.
    Returns (lines, error). On error, `lines` is empty and `error` is the
    human-facing message describing what went wrong."""
    lines = []
    total = 0.0
    for i, lp in enumerate(line_payloads, start=1):
        info = ProductRepository.get_variant(lp.product_id, lp.variant_id)
        if not info:
            return [], 0.0, f"Line {i}: product or variant not found."
        unit = info.get("discount_price")
        if unit is None:
            unit = info["price"]
        if info["stock"] < lp.qty:
            return [], 0.0, (
                f"Line {i}: only {info['stock']} in stock for "
                f"'{info['product_name']}', requested {lp.qty}."
            )
        line_total = round(float(unit) * lp.qty, 2)
        total += line_total

        # Fetch a fuller variant view just for labelling. get_variant only
        # returned the projected variant with price/stock, so we hit the doc
        # once more if we need size/color. This is cheap and one-shot at
        # order placement.
        p = ProductRepository.by_id(lp.product_id)
        variant = next((v for v in p["variants"] if v["id"] == lp.variant_id), None)
        lines.append(
            {
                "product_id": info["product_id"],
                "product_name": info["product_name"],
                "variant_id": info["variant_id"],
                "variant_label": _variant_label(variant) if variant else None,
                "qty": lp.qty,
                "unit_price": float(unit),
                "line_total": line_total,
            }
        )
    return lines, round(total, 2), None


def decrement_inventory_for(lines):
    """Best-effort atomic decrement for every line. If any line fails
    (concurrent underflow), rolls back the ones already decremented and
    returns the failing line's error message."""
    applied = []
    for i, line in enumerate(lines, start=1):
        after = ProductRepository.adjust_stock(
            line["product_id"], line["variant_id"], -line["qty"]
        )
        if after is None:
            for a in applied:
                ProductRepository.adjust_stock(
                    a["product_id"], a["variant_id"], a["qty"]
                )
            return (
                f"Line {i}: stock changed since order was placed — "
                f"cannot decrement {line['qty']} × {line['product_name']}."
            )
        applied.append(line)
    return None
