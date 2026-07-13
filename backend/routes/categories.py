from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_any_user, require_office
from repository.category_repo import CategoryRepository
from repository.subcategory_repo import SubcategoryRepository
from schemas.category import (
    Category,
    CategoryCreate,
    CategoryListResponse,
    CategoryUpdate,
)
from services.audit_service import record

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=CategoryListResponse)
async def list_categories(
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    _=Depends(require_any_user),
):
    skip = (page - 1) * page_size
    items, total = CategoryRepository.list(search=search, skip=skip, limit=page_size)
    return CategoryListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{cat_id}", response_model=Category)
async def get_category(cat_id: str, _=Depends(require_any_user)):
    cat = CategoryRepository.by_id(cat_id)
    if not cat:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    return cat


@router.post("", response_model=Category, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate,
    request: Request,
    current=Depends(require_office),
):
    if CategoryRepository.by_name(payload.name):
        raise HTTPException(status.HTTP_409_CONFLICT, "Category name already exists")
    cat = CategoryRepository.insert(payload.name, payload.description)
    record(
        AuditAction.CATEGORY_CREATE,
        ResourceType.CATEGORY,
        resource_id=cat["_id"],
        actor=current["user"],
        after={"name": cat["name"]},
        request=request,
    )
    return cat


@router.patch("/{cat_id}", response_model=Category)
async def update_category(
    cat_id: str,
    payload: CategoryUpdate,
    request: Request,
    current=Depends(require_office),
):
    before = CategoryRepository.by_id(cat_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    patch = payload.model_dump(exclude_unset=True)
    after = CategoryRepository.update(cat_id, patch)
    # Keep denormalised category_name in sync on child subcategories AND on
    # every product tagged with this category.
    if "name" in patch and patch["name"] and patch["name"] != before["name"]:
        SubcategoryRepository.refresh_category_name(cat_id, patch["name"])
        from repository.product_repo import ProductRepository
        ProductRepository.refresh_taxonomy_names(
            category_id=cat_id, category_name=patch["name"]
        )
    record(
        AuditAction.CATEGORY_UPDATE,
        ResourceType.CATEGORY,
        resource_id=cat_id,
        actor=current["user"],
        before={k: before.get(k) for k in patch},
        after={k: after.get(k) for k in patch},
        request=request,
    )
    return after


@router.delete("/{cat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    cat_id: str,
    request: Request,
    current=Depends(require_office),
):
    target = CategoryRepository.by_id(cat_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    # Refuse to delete a category that still has subcategories — the office
    # should delete or move the subcategories first, otherwise they'd
    # dangle with a stale category_id.
    remaining = SubcategoryRepository.list(category_id=cat_id, limit=1)[1]
    if remaining:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot delete a category that still has subcategories. "
            "Delete or move them first.",
        )
    CategoryRepository.delete(cat_id)
    record(
        AuditAction.CATEGORY_DELETE,
        ResourceType.CATEGORY,
        resource_id=cat_id,
        actor=current["user"],
        before={"name": target["name"]},
        request=request,
    )
    return None
