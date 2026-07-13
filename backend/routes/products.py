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
from repository.product_repo import ProductRepository
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
    return ProductListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{product_id}", response_model=Product)
async def get_product(product_id: str, _=Depends(require_any_user)):
    p = ProductRepository.by_id(product_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return p


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
    )
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
    return p


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

    # Moving the product to a different subcategory: re-resolve its parent
    # so category_id + denormalised names stay consistent.
    if "subcategory_id" in patch and patch["subcategory_id"] is not None:
        sub, cat = _resolve_subcategory(patch["subcategory_id"])
        patch["subcategory_name"] = sub["name"]
        patch["category_id"] = cat["_id"]
        patch["category_name"] = cat["name"]

    after = ProductRepository.update(product_id, patch)
    record(
        AuditAction.PRODUCT_UPDATE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        before={k: before.get(k) for k in patch},
        after={k: after.get(k) for k in patch},
        request=request,
    )
    return after


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
    if not ProductRepository.by_id(product_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    after = ProductRepository.add_variant(product_id, payload.model_dump())
    record(
        AuditAction.VARIANT_CREATE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        after=payload.model_dump(),
        request=request,
    )
    return after


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
    after = ProductRepository.update_variant(product_id, variant_id, patch)
    if after is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Variant not found")
    record(
        AuditAction.VARIANT_UPDATE,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        after={"variant_id": variant_id, **patch},
        request=request,
    )
    return after


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
    if payload.delta == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Delta cannot be zero")
    after = ProductRepository.adjust_stock(product_id, variant_id, payload.delta)
    if after is None:
        # Either the (product, variant) pair doesn't exist, or the negative
        # delta would drop stock below zero.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Adjustment refused: variant not found or would go below zero.",
        )
    record(
        AuditAction.VARIANT_STOCK_ADJUST,
        ResourceType.PRODUCT,
        resource_id=product_id,
        actor=current["user"],
        after={
            "variant_id": variant_id,
            "delta": payload.delta,
            "reason": payload.reason,
        },
        request=request,
    )
    return after


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
