from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


FIELD_LABELS = {
    "email": "Email",
    "password": "Password",
    "current_password": "Current password",
    "new_password": "New password",
    "name": "Name",
    "role": "Role",
    "status": "Status",
    "phone": "Phone",
    "category_id": "Category",
    "subcategory_id": "Subcategory",
    "base_price": "Base price",
    "discount_price": "Discount price",
    "variants": "Variants",
    "location": "Location",
    "gst_number": "GST number",
    "geo.lat": "Latitude",
    "geo.lng": "Longitude",
    "lat": "Latitude",
    "lng": "Longitude",
    "credit_limit": "Credit limit",
    "store_id": "Store",
    "product_id": "Product",
    "variant_id": "Variant",
    "qty": "Quantity",
    "lines": "Order lines",
    "reason": "Reason",
    "delta": "Delta",
}


def _humanize(field):
    if not field:
        return "This field"
    return field.replace("_", " ").strip().capitalize()


def _field_label(loc):
    parts = [str(p) for p in loc if isinstance(p, str) and p != "body"]
    if not parts:
        return "This field"
    key = ".".join(parts)
    return (
        FIELD_LABELS.get(key)
        or FIELD_LABELS.get(parts[-1])
        or _humanize(parts[-1])
    )


def _rewrite_msg(err):
    t = err.get("type", "")
    msg = err.get("msg", "") or ""
    ctx = err.get("ctx", {}) or {}

    if t == "missing":
        return "is required"
    if t.startswith("string_too_short"):
        limit = ctx.get("min_length")
        if limit == 1:
            return "cannot be empty"
        if limit:
            return f"must be at least {limit} characters"
    if t.startswith("string_too_long"):
        limit = ctx.get("max_length")
        if limit:
            return f"must be at most {limit} characters"
    if t in {"less_than", "less_than_equal", "greater_than", "greater_than_equal"}:
        limit = ctx.get("le") or ctx.get("lt") or ctx.get("ge") or ctx.get("gt")
        if t == "less_than_equal":
            return f"must be {limit} or less"
        if t == "less_than":
            return f"must be less than {limit}"
        if t == "greater_than_equal":
            return f"must be {limit} or more"
        if t == "greater_than":
            return f"must be greater than {limit}"
    if t == "enum":
        options = ctx.get("expected", "")
        return f"must be one of {options}" if options else "has an invalid value"
    if t.startswith("type_error") or t in {"int_parsing", "float_parsing", "bool_parsing"}:
        return "is not the right type"
    if t == "value_error":
        return msg.removeprefix("Value error, ") or "is not valid"
    if t == "json_invalid":
        return "is not valid JSON"

    trimmed = msg.strip().rstrip(".")
    if trimmed:
        return trimmed[0].lower() + trimmed[1:]
    return "is invalid"


def _is_whole_object_error(loc):
    """A model_validator error has no field path — its loc is empty (or just
    the body wrapper). Rendering "This field <msg>" reads awkwardly for
    those; the raw message is already a full sentence."""
    parts = [p for p in (loc or ()) if isinstance(p, str) and p != "body"]
    return not parts


def format_validation_errors(errors):
    seen = set()
    lines = []
    for err in errors or []:
        loc = err.get("loc") or ()
        detail = _rewrite_msg(err)
        if _is_whole_object_error(loc):
            # Uppercase-first, single sentence — no field label prefix.
            sentence = detail.strip().rstrip(".")
            if sentence:
                sentence = sentence[0].upper() + sentence[1:]
                line = f"{sentence}."
            else:
                line = "Some fields are invalid."
            key = ("_root_", sentence)
        else:
            label = _field_label(loc)
            key = (label, detail)
            line = f"{label} {detail}."
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    if not lines:
        return "Some fields are invalid."
    return " ".join(lines)


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": format_validation_errors(exc.errors())},
    )
