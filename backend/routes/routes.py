"""Delivery route optimization + bulk dispatch."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from enums.audit import AuditAction, ResourceType
from enums.order import ORDER_TRANSITIONS, OrderStatus
from middleware.auth import require_office
from repository.order_repo import OrderRepository
from schemas.route import (
    BulkDispatchRequest,
    BulkDispatchResponse,
    OptimizedRoute,
    PlannedRoute,
    PlannedRouteListResponse,
    RouteOptimizeRequest,
    RoutePlanRequest,
)
from services import notification_service, route_service
from services.audit_service import record
from services.route_service import create_route


router = APIRouter(prefix="/delivery", tags=["delivery"])


@router.post("/plan", response_model=PlannedRoute)
async def plan_delivery_route(
    payload: RoutePlanRequest,
    request: Request,
    current=Depends(require_office),
):
    """Office-friendly route planning. Takes raw addresses (lat/lng
    required per stop) and returns an optimized visit order.

    Note: routes are computed against straight-line ("as-the-crow-flies")
    haversine distance, not real road networks. `total_distance_km` is
    the great-circle sum; `total_duration_minutes` is a rough estimate
    at ~25 km/h. `polyline` stays null. Good enough to rank stops in
    urban runs; swap to OSRM/Mapbox later for road-aware routing."""
    result = route_service.plan_route(
        start=payload.start.model_dump(),
        stops=[s.model_dump() for s in payload.stops],
        driver_id=payload.driver_id,
        driver_name=payload.driver_name,
        label=payload.label,
        actor=current["user"],
    )
    record(
        AuditAction.ROUTE_CREATE,
        ResourceType.DELIVERY_ROUTE,
        resource_id=result["_id"],
        actor=current["user"],
        after={
            "label": payload.label,
            "stops": len(result["stops"]),
            "total_distance_km": result["total_distance_km"],
            "driver_id": result.get("driver_id"),
        },
        request=request,
    )
    return result


@router.get("/routes", response_model=PlannedRouteListResponse)
async def list_delivery_routes(
    driver_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _=Depends(require_office),
):
    """Saved delivery routes, most-recent first."""
    items, total = route_service.list_routes(
        driver_id=driver_id, page=page, page_size=page_size,
    )
    return PlannedRouteListResponse(
        items=items, total=total, page=page, page_size=page_size,
    )


@router.get("/routes/{route_id}", response_model=PlannedRoute)
async def get_delivery_route(route_id: str, _=Depends(require_office)):
    route = route_service.by_id(route_id)
    if not route:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Route not found")
    return route


@router.post("/optimize", response_model=OptimizedRoute)
async def optimize_route(
    payload: RouteOptimizeRequest,
    request: Request,
    current=Depends(require_office),
):
    """Greedy nearest-neighbor TSP heuristic over the given stops from
    `start`. Persists the generated route with driver info + timestamps.
    Returns the ordered stops with a 1-indexed `sequence` field plus
    total distance (km) and estimated duration (minutes at ~25 km/h)."""
    result = create_route(
        start=payload.start.model_dump(),
        stops=[s.model_dump() for s in payload.stops],
        driver_id=payload.driver_id,
        driver_name=payload.driver_name,
        actor=current["user"],
    )
    record(
        AuditAction.ROUTE_CREATE,
        ResourceType.DELIVERY_ROUTE,
        resource_id=result["_id"],
        actor=current["user"],
        after={
            "stops": len(result["stops"]),
            "total_distance_km": result["total_distance_km"],
            "driver_id": result.get("driver_id"),
        },
        request=request,
    )
    return result


@router.post("/dispatch", response_model=BulkDispatchResponse)
async def bulk_dispatch(
    payload: BulkDispatchRequest,
    request: Request,
    current=Depends(require_office),
):
    """Move a batch of packed orders to out_for_delivery in one call.
    Each order is validated independently — an order not in `packing`
    is skipped with a reason, the rest still get dispatched."""
    dispatched_ids = []
    skipped = []
    for oid in payload.order_ids:
        order = OrderRepository.by_id(oid)
        if not order:
            skipped.append({"order_id": oid, "reason": "not_found"})
            continue
        allowed = ORDER_TRANSITIONS.get(order["status"], ())
        if OrderStatus.OUT_FOR_DELIVERY.value not in allowed:
            skipped.append({
                "order_id": oid,
                "reason": f"illegal_transition_from_{order['status']}",
            })
            continue
        after = OrderRepository.set_status(
            oid, OrderStatus.OUT_FOR_DELIVERY.value, current["user"],
            note="Bulk dispatch",
        )
        dispatched_ids.append(oid)
        notification_service.notify_order_status(
            order=after, prev_status=order["status"], actor=current["user"],
        )
    record(
        AuditAction.ORDER_BULK_DISPATCH,
        ResourceType.ORDER,
        actor=current["user"],
        after={
            "dispatched": len(dispatched_ids),
            "skipped": len(skipped),
            "dispatched_ids": dispatched_ids,
        },
        request=request,
    )
    return BulkDispatchResponse(
        dispatched_count=len(dispatched_ids),
        skipped_count=len(skipped),
        dispatched_ids=dispatched_ids,
        skipped=skipped,
    )
