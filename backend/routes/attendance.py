from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from config.config import settings
from enums.audit import AuditAction, ResourceType
from enums.store import StoreStatus
from helpers.geo import haversine_meters
from middleware.auth import require_any_user, require_sales_rep
from repository.attendance_repo import AttendanceRepository
from repository.store_repo import StoreRepository
from schemas.attendance import Attendance, AttendanceListResponse, AttendanceMark
from services.audit_service import record

router = APIRouter(prefix="/attendance", tags=["attendance"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


@router.post("", response_model=Attendance, status_code=status.HTTP_201_CREATED)
async def mark_attendance(
    payload: AttendanceMark,
    request: Request,
    current=Depends(require_sales_rep),
):
    user = current["user"]
    store = StoreRepository.by_id(payload.store_id)
    if not store or store["owner_id"] != user["_id"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Store not found or does not belong to you.",
        )
    if store["status"] != StoreStatus.APPROVED.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Store is not approved yet — attendance can only be marked at approved stores.",
        )

    geo = store.get("geo") or {}
    if "lat" not in geo or "lng" not in geo:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Store has no saved geo location. Ask office to update the store first.",
        )
    distance = haversine_meters(payload.lat, payload.lng, geo["lat"], geo["lng"])
    if distance > settings.ATTENDANCE_GEOFENCE_METERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"You are {int(distance)} m from the store; must be within "
            f"{int(settings.ATTENDANCE_GEOFENCE_METERS)} m to mark attendance.",
        )

    entry = AttendanceRepository.insert(
        user_id=user["_id"],
        user_name=user.get("name"),
        store_id=store["_id"],
        store_name=store["name"],
        lat=payload.lat,
        lng=payload.lng,
        distance_meters=distance,
        notes=payload.notes,
    )
    record(
        AuditAction.ATTENDANCE_MARK,
        ResourceType.ATTENDANCE,
        resource_id=entry["_id"],
        actor=user,
        after={"store_id": store["_id"], "distance_meters": distance},
        request=request,
    )
    return entry


@router.get("", response_model=AttendanceListResponse)
async def list_attendance(
    store_id: str | None = Query(None),
    user_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current=Depends(require_any_user),
):
    user = current["user"]
    # Sales reps can only see their own attendance regardless of the filter.
    effective_user = user_id if _is_office(user) else user["_id"]
    skip = (page - 1) * page_size
    items, total = AttendanceRepository.list(
        user_id=effective_user, store_id=store_id, skip=skip, limit=page_size
    )
    return AttendanceListResponse(items=items, total=total, page=page, page_size=page_size)
