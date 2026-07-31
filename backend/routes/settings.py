from fastapi import APIRouter, Depends, Request

from enums.audit import AuditAction, ResourceType
from middleware.auth import require_any_user, require_developer
from repository.settings_repo import ClientSettingsRepository
from schemas.settings import ClientSettings, ClientSettingsUpdate
from services.audit_service import record


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=ClientSettings)
async def get_client_settings(_=Depends(require_any_user)):
    """Read the singleton client-settings doc. Every authenticated
    user can read it — the frontend uses this to render branding,
    currency, and theme colors on every screen."""
    return ClientSettingsRepository.get()


@router.patch("", response_model=ClientSettings)
async def update_client_settings(
    payload: ClientSettingsUpdate,
    request: Request,
    current=Depends(require_developer),
):
    """Update client settings. Developer role only (admin also allowed
    for break-glass). Only fields set in the payload are updated;
    unset fields stay at their prior value."""
    patch = payload.model_dump(exclude_unset=True)
    before = ClientSettingsRepository.get()
    after = ClientSettingsRepository.update(patch, actor=current["user"])
    record(
        AuditAction.SETTINGS_UPDATE,
        ResourceType.SETTINGS,
        resource_id="app_settings",
        actor=current["user"],
        before={k: before.get(k) for k in patch},
        after={k: after.get(k) for k in patch},
        request=request,
    )
    return after
