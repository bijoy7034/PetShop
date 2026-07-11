import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_admin, require_any_user
from repository.user_repo import UserRepository
from schemas.user import (
    User,
    UserCreate,
    UserCreateResponse,
    UserListResponse,
    UserUpdate,
)
from services.audit_service import record
from utils.auth import hash_password

router = APIRouter(prefix="/users", tags=["users"])


def _generate_temp_password():
    return secrets.token_urlsafe(12)


@router.get("", response_model=UserListResponse)
async def list_users(
    role: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(require_any_user),
):
    skip = (page - 1) * page_size
    items, total = UserRepository.list(
        role=role,
        status=status_filter,
        search=search,
        skip=skip,
        limit=page_size,
    )
    return UserListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{user_id}", response_model=User)
async def get_user(user_id: str, _=Depends(require_any_user)):
    user = UserRepository.by_id(user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.post("", response_model=UserCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    current=Depends(require_admin),
):
    if UserRepository.by_email(payload.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")


    supplied = payload.password
    temp_password = None
    if supplied:
        pw_hash = hash_password(supplied)
        must_change = False
    else:
        temp_password = _generate_temp_password()
        pw_hash = hash_password(temp_password)
        must_change = True

    user = UserRepository.insert(
        email=payload.email,
        name=payload.name,
        role=payload.role.value,
        password_hash=pw_hash,
        phone=payload.phone,
        must_change_password=must_change,
    )

    record(
        AuditAction.USER_CREATE,
        ResourceType.USER,
        resource_id=user["_id"],
        actor=current["user"],
        after={
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "phone": user.get("phone"),
        },
        request=request,
    )

    return UserCreateResponse(user=user, temporary_password=temp_password)


@router.patch("/{user_id}", response_model=User)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    current=Depends(require_admin),
):
    before = UserRepository.by_id(user_id)
    if not before:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    patch = payload.model_dump(exclude_unset=True)
    if "password" in patch and patch["password"]:
        patch["password_hash"] = hash_password(patch.pop("password"))
    if "role" in patch and patch["role"] is not None:
        patch["role"] = patch["role"].value if hasattr(patch["role"], "value") else patch["role"]
    if "status" in patch and patch["status"] is not None:
        patch["status"] = (
            patch["status"].value if hasattr(patch["status"], "value") else patch["status"]
        )

    after = UserRepository.update(user_id, patch)

    action = AuditAction.USER_UPDATE
    if patch.get("status") == "inactive" and before.get("status") == "active":
        action = AuditAction.USER_DEACTIVATE
    elif patch.get("status") == "active" and before.get("status") == "inactive":
        action = AuditAction.USER_REACTIVATE
    elif "password_hash" in patch and len(patch) == 2:
        action = AuditAction.USER_PASSWORD_RESET

    diff_before = {k: before.get(k) for k in patch if k != "password_hash"}
    diff_after = {k: after.get(k) for k in patch if k != "password_hash"}

    record(
        action,
        ResourceType.USER,
        resource_id=user_id,
        actor=current["user"],
        before=diff_before,
        after=diff_after,
        request=request,
    )
    return after


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    request: Request,
    current=Depends(require_admin),
):
    target = UserRepository.by_id(user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if target["_id"] == current["user"]["_id"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You cannot delete your own account.",
        )

    ok = UserRepository.delete(user_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    record(
        AuditAction.USER_DELETE,
        ResourceType.USER,
        resource_id=user_id,
        actor=current["user"],
        before={
            "email": target["email"],
            "name": target["name"],
            "role": target["role"],
            "phone": target.get("phone"),
            "status": target.get("status"),
        },
        request=request,
    )
    return None
