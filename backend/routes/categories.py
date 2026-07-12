from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_any_user, require_office
from repository.category_repo import CategoryRepository
from schemas.category import (
    Category,
    CategoryCreate,
    CategoryListResponse,
    CategoryUpdate,
    SubcategoryCreate,
    SubcategoryUpdate,
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
    ok = CategoryRepository.delete(cat_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    record(
        AuditAction.CATEGORY_DELETE,
        ResourceType.CATEGORY,
        resource_id=cat_id,
        actor=current["user"],
        before={"name": target["name"]},
        request=request,
    )
    return None


@router.post(
    "/{cat_id}/subcategories",
    response_model=Category,
    status_code=status.HTTP_201_CREATED,
)
async def add_subcategory(
    cat_id: str,
    payload: SubcategoryCreate,
    request: Request,
    current=Depends(require_office),
):
    cat = CategoryRepository.by_id(cat_id)
    if not cat:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    if any(s["name"].lower() == payload.name.lower() for s in cat["subcategories"]):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Subcategory with that name already exists"
        )
    after = CategoryRepository.add_subcategory(cat_id, payload.name)
    record(
        AuditAction.SUBCATEGORY_CREATE,
        ResourceType.CATEGORY,
        resource_id=cat_id,
        actor=current["user"],
        after={"name": payload.name},
        request=request,
    )
    return after


@router.patch(
    "/{cat_id}/subcategories/{sub_id}",
    response_model=Category,
)
async def update_subcategory(
    cat_id: str,
    sub_id: str,
    payload: SubcategoryUpdate,
    request: Request,
    current=Depends(require_office),
):
    if not CategoryRepository.has_subcategory(cat_id, sub_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subcategory not found")
    if payload.name is None:
        return CategoryRepository.by_id(cat_id)
    after = CategoryRepository.update_subcategory(cat_id, sub_id, payload.name)
    record(
        AuditAction.SUBCATEGORY_UPDATE,
        ResourceType.CATEGORY,
        resource_id=cat_id,
        actor=current["user"],
        after={"sub_id": sub_id, "name": payload.name},
        request=request,
    )
    return after


@router.delete(
    "/{cat_id}/subcategories/{sub_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_subcategory(
    cat_id: str,
    sub_id: str,
    request: Request,
    current=Depends(require_office),
):
    if not CategoryRepository.has_subcategory(cat_id, sub_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subcategory not found")
    CategoryRepository.remove_subcategory(cat_id, sub_id)
    record(
        AuditAction.SUBCATEGORY_DELETE,
        ResourceType.CATEGORY,
        resource_id=cat_id,
        actor=current["user"],
        before={"sub_id": sub_id},
        request=request,
    )
    return None
