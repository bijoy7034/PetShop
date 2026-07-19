from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from config.config import settings
from enums.audit import AuditAction, ResourceType
from enums.store import StoreStatus
from enums.visit import VisitMode, VisitOutcome
from helpers.geo import haversine_meters
from middleware.auth import require_any_user, require_sales_rep
from repository.order_repo import OrderRepository
from repository.store_repo import StoreRepository
from repository.visit_repo import VisitRepository
from schemas.visit import Visit, VisitListResponse, VisitMark
from services.audit_service import record

router = APIRouter(prefix="/visits", tags=["visits"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


def _location_snapshot(store):
    geo = store.get("geo") or {}
    return {
        "location": store.get("location"),
        "district": store.get("district"),
        "geo_lat": geo.get("lat"),
        "geo_lng": geo.get("lng"),
    }


@router.post("", response_model=Visit, status_code=status.HTTP_201_CREATED)
async def mark_visit(
    payload: VisitMark,
    request: Request,
    current=Depends(require_sales_rep),
):
    """Log a field visit. Two modes:
    - in_store: GPS mandatory, geo-fence enforced.
    - remote: GPS optional, distance check skipped (for phone/whatsapp
      orders).
    Outcome:
    - order_placed: order_id required and verified to belong to this rep
      and this store; order_value copied from the order's total.
    - no_order: no_order_reason required.
    Multiple visits per day per store are allowed.
    """
    user = current["user"]
    store = StoreRepository.by_id(payload.store_id)
    if not store or store.get("sales_rep_id") != user["_id"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Store not found or is not assigned to you.",
        )
    if store["status"] != StoreStatus.APPROVED.value:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Store is not approved — visits can only be logged at approved stores.",
        )

    distance = None
    if payload.mode == VisitMode.IN_STORE:
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
                f"{int(settings.ATTENDANCE_GEOFENCE_METERS)} m to log an in-store visit.",
            )

    order_value = None
    if payload.outcome == VisitOutcome.ORDER_PLACED:
        order = OrderRepository.by_id(payload.order_id)
        if not order:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Order not found.")
        if order.get("sales_rep_id") != user["_id"]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That order was placed by a different sales rep.",
            )
        if order.get("store_id") != store["_id"]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That order is for a different store.",
            )
        order_value = float(order.get("total") or 0)

    duration_minutes = None
    if payload.check_in and payload.check_out:
        delta = payload.check_out - payload.check_in
        duration_minutes = int(round(delta.total_seconds() / 60))

    entry = VisitRepository.insert(
        sales_rep_id=user["_id"],
        sales_rep_name=user.get("name"),
        store_id=store["_id"],
        store_code=store.get("code"),
        store_name=store["name"],
        location_snapshot=_location_snapshot(store),
        mode=payload.mode.value,
        outcome=payload.outcome.value,
        lat=payload.lat,
        lng=payload.lng,
        distance_meters=distance,
        check_in=payload.check_in,
        check_out=payload.check_out,
        duration_minutes=duration_minutes,
        order_id=payload.order_id,
        order_value=order_value,
        no_order_reason=payload.no_order_reason,
        remarks=payload.remarks,
    )
    record(
        AuditAction.VISIT_MARK,
        ResourceType.VISIT,
        resource_id=entry["_id"],
        actor=user,
        after={
            "store_id": store["_id"],
            "mode": payload.mode.value,
            "outcome": payload.outcome.value,
            "order_id": payload.order_id,
            "distance_meters": distance,
        },
        request=request,
    )
    return entry


@router.get("", response_model=VisitListResponse)
async def list_visits(
    store_id: str | None = Query(None),
    user_id: str | None = Query(None),
    visit_date: str | None = Query(None, description="YYYY-MM-DD"),
    mode: str | None = Query(None),
    outcome: str | None = Query(None),
    district: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current=Depends(require_any_user),
):
    user = current["user"]
    effective_user = user_id if _is_office(user) else user["_id"]
    skip = (page - 1) * page_size
    items, total = VisitRepository.list(
        sales_rep_id=effective_user,
        store_id=store_id,
        visit_date=visit_date,
        mode=mode,
        outcome=outcome,
        district=district,
        skip=skip,
        limit=page_size,
    )
    return VisitListResponse(items=items, total=total, page=page, page_size=page_size)
