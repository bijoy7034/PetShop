from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_any_user, require_office
from repository.category_repo import CategoryRepository
from repository.subcategory_repo import SubcategoryRepository
from schemas.subcategory import (
    Subcategory,
    SubcategoryCreate,
    SubcategoryListResponse,
    SubcategoryUpdate,
)
from services.audit_service import record

router = APIRouter(prefix="/subcategories", tags=["subcategories"])


def _require_category(category_id):
    cat = CategoryRepository.by_id(category_id)
    if not cat:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown category")
    return cat


@router.get("", response_model=SubcategoryListResponse)
async def list_subcategories(
    category_id: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    _=Depends(require_any_user),
):
    skip = (page - 1) * page_size
    items, total = SubcategoryRepository.list(
        category_id=category_id, search=search, skip=skip, limit=page_size
    )
    return SubcategoryListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/{sub_id}", response_model=Subcategory)
async def get_subcategory(sub_id: str, _=Depends(require_any_user)):
    sub = SubcategoryRepository.by_id(sub_id)
    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subcategory not found")
    return sub


@router.post("", response_model=Subcategory, status_code=status.HTTP_201_CREATED)
async def create_subcategory(
    payload: SubcategoryCreate,
    request: Request,
    current=Depends(require_office),
):
    cat = _require_category(payload.category_id)
    if SubcategoryRepository.by_name_in_category(payload.name, payload.category_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A subcategory with that name already exists in this category.",
        )
    sub = SubcategoryRepository.insert(
        name=payload.name,
        category_id=payload.category_id,
        category_name=cat["name"],
        description=payload.description,
    )
    record(
        AuditAction.SUBCATEGORY_CREATE,
        ResourceType.CATEGORY,
        resource_id=sub["_id"],
        actor=current["user"],
        after={"name": sub["name"], "category_id": sub["category_id"]},
        request=request,
    )
    return sub


@router.patch("/{sub_id}", response_model=Subcategory)
async def update_subcategory(
    sub_id: str,
    payload: SubcategoryUpdate,
    request: Request,
    current=Depends(require_office),
):
    before = SubcategoryRepository.by_id(sub_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subcategory not found")
    patch = payload.model_dump(exclude_unset=True)

    # Moving to another category re-scopes the uniqueness check and rewrites
    # the denormalised category_name.
    if "category_id" in patch and patch["category_id"] is not None:
        cat = _require_category(patch["category_id"])
        patch["category_name"] = cat["name"]
    target_name = patch.get("name", before["name"])
    target_cat = patch.get("category_id", before["category_id"])
    dup = SubcategoryRepository.by_name_in_category(target_name, target_cat)
    if dup and dup["_id"] != sub_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A subcategory with that name already exists in this category.",
        )

    after = SubcategoryRepository.update(sub_id, patch)
    # Propagate a rename (and any category-change) to every product tagged
    # with this subcategory.
    if after and (
        after.get("name") != before.get("name")
        or after.get("category_id") != before.get("category_id")
    ):
        from repository.product_repo import ProductRepository
        ProductRepository.refresh_taxonomy_names(
            subcategory_id=sub_id,
            subcategory_name=after["name"],
            category_id=after["category_id"],
            category_name=after.get("category_name"),
        )
    record(
        AuditAction.SUBCATEGORY_UPDATE,
        ResourceType.CATEGORY,
        resource_id=sub_id,
        actor=current["user"],
        before={k: before.get(k) for k in patch},
        after={k: after.get(k) for k in patch},
        request=request,
    )
    return after


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subcategory(
    sub_id: str,
    request: Request,
    current=Depends(require_office),
):
    before = SubcategoryRepository.by_id(sub_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subcategory not found")
    SubcategoryRepository.delete(sub_id)
    record(
        AuditAction.SUBCATEGORY_DELETE,
        ResourceType.CATEGORY,
        resource_id=sub_id,
        actor=current["user"],
        before={"name": before["name"], "category_id": before["category_id"]},
        request=request,
    )
    return None
