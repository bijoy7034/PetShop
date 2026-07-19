from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_any_user, require_office
from repository.category_repo import CategoryRepository
from repository.inventory_repo import InventoryRepository
from repository.product_repo import ProductRepository, variant_label
from repository.subcategory_repo import SubcategoryRepository
from schemas.product import (
    BulkUploadResponse,
    Product,
    ProductCreate,
    ProductListResponse,
    ProductUpdate,
    StockAdjust,
    VariantCreate,
    VariantUpdate,
)
from services.audit_service import record
from services.product_service import expand_option_sets, import_products

router = APIRouter(prefix="/products", tags=["products"])

_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


def _resolve_subcategory(subcategory_id):
    """Look up the subcategory and its parent category. Returns the pair
    (sub_doc, cat_doc). Raises 400 if either is missing."""
    sub = SubcategoryRepository.by_id(subcategory_id)
    if not sub:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown subcategory")
    cat = CategoryRepository.by_id(sub["category_id"])
    if not cat:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Subcategory points at a category that no longer exists.",
        )
    return sub, cat


def _seed_inventory_for(product, seeds):
    """After a product is inserted (or a variant added), create the matching
    inventory rows. Idempotent per-variant."""
    if not seeds:
        return
    for s in seeds:
        InventoryRepository.create(
            product_id=product["_id"],
            variant_id=s["variant_id"],
            variant_label=s.get("variant_label"),
            product_name=product["name"],
            quantity_on_hand=s.get("initial_stock") or 0,
            reorder_level=s.get("reorder_level") or 0,
        )


def _with_inventory(product):
    """Hydrate each variant with live inventory counts. One find() covers all
    variants of one product."""
    if not product:
        return product
    variants = product.get("variants") or []
    variant_ids = [v["id"] for v in variants]
    counts = InventoryRepository.by_variant_ids(variant_ids)
    for v in variants:
        inv = counts.get(v["id"])
        if inv:
            v["quantity_on_hand"] = inv["quantity_on_hand"]
            v["reserved_quantity"] = inv["reserved_quantity"]
            v["available"] = inv["available"]
            v["reorder_level"] = inv["reorder_level"]
    return product


def _many_with_inventory(products):
    if not products:
        return products
    all_variant_ids = [v["id"] for p in products for v in p.get("variants") or []]
    counts = InventoryRepository.by_variant_ids(all_variant_ids)
    for p in products:
        for v in p.get("variants") or []:
            inv = counts.get(v["id"])
            if inv:
                v["quantity_on_hand"] = inv["quantity_on_hand"]
                v["reserved_quantity"] = inv["reserved_quantity"]
                v["available"] = inv["available"]
                v["reorder_level"] = inv["reorder_level"]
    return products


@router.get("", response_model=ProductListResponse)
async def list_products(
    category_id: str | None = Query(None),
    subcategory_id: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(require_any_user),
):
    skip = (page - 1) * page_size
    items, total = ProductRepository.list(
        category_id=category_id,
        subcategory_id=subcategory_id,
        search=search,
        skip=skip,
        limit=page_size,
    )
    items = _many_with_inventory(items)
    return ProductListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{product_id}", response_model=Product)
async def get_product(product_id: str, _=Depends(require_any_user)):
    p = ProductRepository.by_id(product_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return _with_inventory(p)


@router.post("", response_model=Product, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    request: Request,
    current=Depends(require_office),
):
    sub, cat = _resolve_subcategory(payload.subcategory_id)
    option_dump = payload.option_sets.model_dump() if payload.option_sets else None
    variants = expand_option_sets(option_dump, payload.base_price)
    p = ProductRepository.insert(
        name=payload.name,
        subcategory_id=sub["_id"],
        subcategory_name=sub["name"],
        category_id=cat["_id"],
        category_name=cat["name"],
        description=payload.description,
        base_price=payload.base_price,
        discount_price=payload.discount_price,
        variants=variants,
        tags=payload.tags,
        brand=payload.brand,
        barcode=payload.barcode,
        cost_price=payload.cost_price,
        tax_rate=payload.tax_rate,
        is_featured=payload.is_featured,
        is_refundable=payload.is_refundable,
        is_returnable=payload.is_returnable,
    )
    seeds = p.pop("_inventory_seed", [])
    _seed_inventory_for(p, seeds)
    record(
        AuditAction.PRODUCT_CREATE,
        ResourceType.PRODUCT,
        resource_id=p["_id"],
        actor=current["user"],
        after={
            "name": p["name"],
            "subcategory_id": p["subcategory_id"],
            "category_id": p["category_id"],
            "variant_count": len(variants),
        },
        request=request,
    )
    return _with_inventory(p)


@router.patch("/{product_id}", response_model=Product)
async def update_product(
    product_id: str,
    payload: ProductUpdate,
    request: Request,
    current=Depends(require_office),
):
    before = ProductRepository.by_id(product_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    patch = payload.model_dump(exclude_unset=True)

    if "subcategory_id" in patch and patch["subcategory_id"] is not None:
        sub, cat = _resolve_subcategory(patch["subcategory_id"])
        patch["subcategory_name"] = sub["name"]
        patch["category_id"] = cat["_id"]
        patch["category_name"] = cat["name"]

    after = ProductRepository.update(product_id, patch)
    # Keep denormalised product_name on every inventory row in sync.
    if "name" in patch and patch["name"] and patch["name"] != before["name"]:
        InventoryRepository.refresh_product_name(product_id, patch["name"])
    record(
        AuditAction.PRODUCT_UPDATE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        before={k: before.get(k) for k in patch},
        after={k: after.get(k) for k in patch},
        request=request,
    )
    return _with_inventory(after)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: str,
    request: Request,
    current=Depends(require_office),
):
    before = ProductRepository.by_id(product_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    ProductRepository.delete(product_id)
    InventoryRepository.delete_by_product(product_id)
    record(
        AuditAction.PRODUCT_DELETE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        before={"name": before["name"]},
        request=request,
    )
    return None


@router.post(
    "/{product_id}/variants",
    response_model=Product,
    status_code=status.HTTP_201_CREATED,
)
async def add_variant(
    product_id: str,
    payload: VariantCreate,
    request: Request,
    current=Depends(require_office),
):
    p = ProductRepository.by_id(product_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    after, seed = ProductRepository.add_variant(product_id, payload.model_dump())
    if seed:
        InventoryRepository.create(
            product_id=product_id,
            variant_id=seed["variant_id"],
            variant_label=seed["variant_label"],
            product_name=after["name"],
            quantity_on_hand=seed["initial_stock"],
            reorder_level=seed["reorder_level"],
        )
    record(
        AuditAction.VARIANT_CREATE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        after={**payload.model_dump(), "variant_id": seed["variant_id"] if seed else None},
        request=request,
    )
    return _with_inventory(after)


@router.patch(
    "/{product_id}/variants/{variant_id}",
    response_model=Product,
)
async def update_variant(
    product_id: str,
    variant_id: str,
    payload: VariantUpdate,
    request: Request,
    current=Depends(require_office),
):
    patch = payload.model_dump(exclude_unset=True)
    # `reason` isn't a stored variant field — it decorates the price_history
    # entry the repo pushes when price or discount_price actually change.
    reason = patch.pop("reason", None)
    after = ProductRepository.update_variant(
        product_id, variant_id, patch, actor=current["user"], reason=reason
    )
    if after is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Variant not found")
    # If any of the identity fields changed, refresh the label on the
    # inventory row so reports stay readable.
    if any(k in patch for k in ("size", "weight", "color", "sku")):
        variant = next((v for v in after["variants"] if v["id"] == variant_id), None)
        if variant:
            InventoryRepository.refresh_labels(
                variant_id, variant_label=variant_label(variant)
            )
    record(
        AuditAction.VARIANT_UPDATE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        after={"variant_id": variant_id, **patch, "reason": reason},
        request=request,
    )
    return _with_inventory(after)


@router.patch("/{product_id}/toggle-active", response_model=Product)
async def toggle_product_active(
    product_id: str,
    request: Request,
    current=Depends(require_office),
):
    """Flip a product's is_active flag. Inactive products (and all their
    variants) can't be added to new orders."""
    before = ProductRepository.by_id(product_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    after = ProductRepository.toggle_active(product_id)
    record(
        AuditAction.PRODUCT_UPDATE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        before={"is_active": before.get("is_active", True)},
        after={"is_active": after["is_active"]},
        request=request,
    )
    return _with_inventory(after)


@router.patch(
    "/{product_id}/variants/{variant_id}/toggle-active",
    response_model=Product,
)
async def toggle_variant_active(
    product_id: str,
    variant_id: str,
    request: Request,
    current=Depends(require_office),
):
    """Flip a specific variant's is_active flag independently of its
    parent product. Inactive variants can't be added to new orders."""
    before = ProductRepository.by_id(product_id)
    if not before or not any(v["id"] == variant_id for v in before["variants"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Variant not found")
    after = ProductRepository.toggle_variant_active(product_id, variant_id)
    if after is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Variant not found")
    before_v = next(v for v in before["variants"] if v["id"] == variant_id)
    after_v = next(v for v in after["variants"] if v["id"] == variant_id)
    record(
        AuditAction.VARIANT_UPDATE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        before={"variant_id": variant_id, "is_active": before_v.get("is_active", True)},
        after={"variant_id": variant_id, "is_active": after_v["is_active"]},
        request=request,
    )
    return _with_inventory(after)


@router.delete(
    "/{product_id}/variants/{variant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_variant(
    product_id: str,
    variant_id: str,
    request: Request,
    current=Depends(require_office),
):
    p = ProductRepository.by_id(product_id)
    if not p or not any(v["id"] == variant_id for v in p["variants"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Variant not found")
    ProductRepository.remove_variant(product_id, variant_id)
    InventoryRepository.delete_by_variant(variant_id)
    record(
        AuditAction.VARIANT_DELETE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        before={"variant_id": variant_id},
        request=request,
    )
    return None


@router.post(
    "/{product_id}/variants/{variant_id}/adjust-stock",
    response_model=Product,
)
async def adjust_variant_stock(
    product_id: str,
    variant_id: str,
    payload: StockAdjust,
    request: Request,
    current=Depends(require_office),
):
    """Legacy URL — delegates to inventory. Stock lives in the inventory
    collection, not on the variant document."""
    if payload.delta == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Delta cannot be zero")
    p = ProductRepository.by_id(product_id)
    if not p or not any(v["id"] == variant_id for v in p["variants"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Variant not found")
    updated = InventoryRepository.adjust_on_hand(variant_id, payload.delta)
    if updated is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Adjustment refused: would push on-hand below reserved quantity.",
        )
    record(
        AuditAction.INVENTORY_ADJUST,
        ResourceType.INVENTORY,
        resource_id=updated["_id"],
        actor=current["user"],
        after={
            "variant_id": variant_id,
            "delta": payload.delta,
            "reason": payload.reason,
            "quantity_on_hand": updated["quantity_on_hand"],
        },
        request=request,
    )
    return _with_inventory(ProductRepository.by_id(product_id))


@router.post("/bulk-upload", response_model=BulkUploadResponse)
async def bulk_upload(
    request: Request,
    current=Depends(require_office),
    file: UploadFile = File(...),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload must be a .xlsx (or .xlsm) workbook.",
        )
    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"File too large. Max is {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    summary = import_products(contents)
    record(
        AuditAction.PRODUCT_BULK_UPLOAD,
        ResourceType.PRODUCT,
        actor=current["user"],
        after={
            "filename": file.filename,
            "created": summary["created"],
            "updated": summary["updated"],
            "failed": summary["failed"],
        },
        request=request,
    )
    return BulkUploadResponse(**summary)
