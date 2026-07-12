"""Excel bulk-upload for products.

The expected sheet columns (case-insensitive, first row is the header):

    name | category | subcategory | description | base_price | discount_price
      | variant_size | variant_weight | variant_color | variant_sku
      | variant_price | variant_discount_price | variant_stock

One row per variant. Rows sharing the same `name` are merged into a single
product; the first row supplies the product-level fields. `category` (and
`subcategory` if given) are resolved by name against existing categories —
unknown categories fail the row rather than being auto-created.
"""
from io import BytesIO

from openpyxl import load_workbook

from repository.category_repo import CategoryRepository
from repository.product_repo import ProductRepository

_HEADER_ALIASES = {
    "name": "name",
    "product": "name",
    "product name": "name",
    "category": "category",
    "subcategory": "subcategory",
    "sub category": "subcategory",
    "sub-category": "subcategory",
    "description": "description",
    "base price": "base_price",
    "base_price": "base_price",
    "discount price": "discount_price",
    "discount_price": "discount_price",
    "variant size": "variant_size",
    "variant_size": "variant_size",
    "size": "variant_size",
    "variant weight": "variant_weight",
    "variant_weight": "variant_weight",
    "weight": "variant_weight",
    "variant color": "variant_color",
    "variant_color": "variant_color",
    "color": "variant_color",
    "variant sku": "variant_sku",
    "variant_sku": "variant_sku",
    "sku": "variant_sku",
    "variant price": "variant_price",
    "variant_price": "variant_price",
    "variant discount price": "variant_discount_price",
    "variant_discount_price": "variant_discount_price",
    "variant stock": "variant_stock",
    "variant_stock": "variant_stock",
    "stock": "variant_stock",
}


def _normalize_headers(header_row):
    out = {}
    for i, raw in enumerate(header_row):
        if raw is None:
            continue
        key = _HEADER_ALIASES.get(str(raw).strip().lower())
        if key:
            out[key] = i
    return out


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _to_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _resolve_category(name):
    if not name:
        return None, None
    cat = CategoryRepository.by_name(name)
    return cat, None if cat else f"Unknown category '{name}'"


def _resolve_subcategory(cat, name):
    if not cat or not name:
        return None, None
    for s in cat.get("subcategories") or []:
        if s["name"].lower() == name.lower():
            return s["id"], None
    return None, f"Unknown subcategory '{name}' in category '{cat['name']}'"


def parse_products_workbook(file_bytes):
    """Return (rows, header_error). Each row is a dict with the raw fields
    plus a `_row` number (1-indexed like Excel)."""
    wb = load_workbook(filename=BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return [], "The uploaded file is empty."

    headers = _normalize_headers(header_row)
    if "name" not in headers:
        return [], "Header row must contain a 'name' column."
    if "category" not in headers:
        return [], "Header row must contain a 'category' column."
    if "variant_price" not in headers and "base_price" not in headers:
        return [], "Header row must contain 'base_price' or 'variant_price'."

    out = []
    for idx, row in enumerate(rows_iter, start=2):
        if row is None or all(c is None or c == "" for c in row):
            continue
        out.append(
            {
                "_row": idx,
                "name": _to_str(_cell(row, headers.get("name"))),
                "category": _to_str(_cell(row, headers.get("category"))),
                "subcategory": _to_str(_cell(row, headers.get("subcategory"))),
                "description": _to_str(_cell(row, headers.get("description"))),
                "base_price": _to_float(_cell(row, headers.get("base_price"))),
                "discount_price": _to_float(_cell(row, headers.get("discount_price"))),
                "variant_size": _to_str(_cell(row, headers.get("variant_size"))),
                "variant_weight": _to_str(_cell(row, headers.get("variant_weight"))),
                "variant_color": _to_str(_cell(row, headers.get("variant_color"))),
                "variant_sku": _to_str(_cell(row, headers.get("variant_sku"))),
                "variant_price": _to_float(_cell(row, headers.get("variant_price"))),
                "variant_discount_price": _to_float(
                    _cell(row, headers.get("variant_discount_price"))
                ),
                "variant_stock": _to_int(_cell(row, headers.get("variant_stock"))),
            }
        )
    return out, None


def import_products(file_bytes):
    rows, header_error = parse_products_workbook(file_bytes)
    if header_error:
        return {
            "created": 0,
            "updated": 0,
            "failed": 0,
            "rows": [{"row": 1, "action": "header_error", "error": header_error}],
        }

    # Merge rows sharing the same name into a single product with N variants.
    by_name = {}
    order = []
    for r in rows:
        if not r["name"]:
            by_name.setdefault(("__missing__", r["_row"]), r)
            order.append(("__missing__", r["_row"]))
            continue
        key = r["name"]
        if key not in by_name:
            by_name[key] = {"first": r, "variants": []}
            order.append(key)
        by_name[key]["variants"].append(r)

    reports = []
    created = updated = failed = 0

    for key in order:
        if isinstance(key, tuple):
            reports.append(
                {"row": key[1], "action": "failed", "error": "Product name is required"}
            )
            failed += 1
            continue
        bundle = by_name[key]
        head = bundle["first"]

        cat, cat_err = _resolve_category(head["category"])
        if cat_err:
            reports.append(
                {"row": head["_row"], "action": "failed", "product_name": key, "error": cat_err}
            )
            failed += len(bundle["variants"])
            continue

        sub_id = None
        if head["subcategory"]:
            sub_id, sub_err = _resolve_subcategory(cat, head["subcategory"])
            if sub_err:
                reports.append(
                    {
                        "row": head["_row"],
                        "action": "failed",
                        "product_name": key,
                        "error": sub_err,
                    }
                )
                failed += len(bundle["variants"])
                continue

        base_price = head["base_price"]
        if base_price is None:
            # Fall back to the first variant's price so simple sheets (single
            # variant per product) still work without a separate base_price
            # column.
            base_price = head["variant_price"]
        if base_price is None:
            reports.append(
                {
                    "row": head["_row"],
                    "action": "failed",
                    "product_name": key,
                    "error": "base_price (or variant_price) is required",
                }
            )
            failed += len(bundle["variants"])
            continue

        variants_payload = []
        for r in bundle["variants"]:
            price = r["variant_price"] if r["variant_price"] is not None else base_price
            variants_payload.append(
                {
                    "size": r["variant_size"],
                    "weight": r["variant_weight"],
                    "color": r["variant_color"],
                    "sku": r["variant_sku"],
                    "price": price,
                    "discount_price": r["variant_discount_price"],
                    "stock": r["variant_stock"] or 0,
                }
            )

        existing = ProductRepository.by_name(key)
        if existing:
            # Update product-level fields and append the new variants. We
            # deliberately do NOT drop variants absent from the sheet — bulk
            # upload is additive; explicit variant delete uses its own route.
            ProductRepository.update(
                existing["_id"],
                {
                    "category_id": cat["_id"],
                    "subcategory_id": sub_id,
                    "description": head["description"],
                    "base_price": base_price,
                    "discount_price": head["discount_price"],
                },
            )
            for v in variants_payload:
                ProductRepository.add_variant(existing["_id"], v)
            updated += 1
            for r in bundle["variants"]:
                reports.append(
                    {"row": r["_row"], "action": "updated", "product_name": key}
                )
        else:
            ProductRepository.insert(
                name=key,
                category_id=cat["_id"],
                subcategory_id=sub_id,
                description=head["description"],
                base_price=base_price,
                discount_price=head["discount_price"],
                variants=variants_payload,
            )
            created += 1
            for r in bundle["variants"]:
                reports.append(
                    {"row": r["_row"], "action": "created", "product_name": key}
                )

    return {"created": created, "updated": updated, "failed": failed, "rows": reports}
