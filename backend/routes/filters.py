"""One endpoint that bundles every dropdown the frontend needs.

Frontends hit this on page load to populate filters (district picker,
rep picker, category tree, status chips). Cheap by design — mostly
`distinct` queries and light projections.
"""
from fastapi import APIRouter, Depends

from enums.achievement import (
    AchievementMetric,
    AchievementPeriod,
    AchievementProgressStatus,
)
from enums.order import OrderStatus, PaymentStatus
from enums.store import StoreStatus
from enums.user import Role
from enums.visit import VisitMode, VisitOutcome
from middleware.auth import require_any_user
from repository.category_repo import CategoryRepository
from repository.store_repo import StoreRepository
from repository.subcategory_repo import SubcategoryRepository
from repository.user_repo import UserRepository
from schemas.filters import FiltersResponse, IdName, SubcategoryFilter


router = APIRouter(prefix="/filters", tags=["filters"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


@router.get("", response_model=FiltersResponse)
async def get_filters(current=Depends(require_any_user)):
    user = current["user"]

    if _is_office(user):
        districts = sorted(_all_districts())
        reps, _ = UserRepository.list(role="sales_rep", status="active", limit=1000)
        sales_reps = [IdName(id=r["_id"], name=r.get("name") or "") for r in reps]
    else:
        districts = sorted(StoreRepository.districts_for_rep(user["_id"]))
        sales_reps = [IdName(id=user["_id"], name=user.get("name") or "")]

    categories, _ = CategoryRepository.list(limit=1000)
    subcategories, _ = SubcategoryRepository.list(limit=2000)

    return FiltersResponse(
        districts=districts,
        sales_reps=sales_reps,
        categories=[
            IdName(id=c["_id"], name=c.get("name") or "") for c in categories
        ],
        subcategories=[
            SubcategoryFilter(
                id=s["_id"],
                name=s.get("name") or "",
                category_id=s.get("category_id") or "",
                category_name=s.get("category_name"),
            )
            for s in subcategories
        ],
        order_statuses=[e.value for e in OrderStatus],
        payment_statuses=[e.value for e in PaymentStatus],
        store_statuses=[e.value for e in StoreStatus],
        visit_modes=[e.value for e in VisitMode],
        visit_outcomes=[e.value for e in VisitOutcome],
        achievement_periods=[e.value for e in AchievementPeriod],
        achievement_metrics=[e.value for e in AchievementMetric],
        achievement_progress_statuses=[e.value for e in AchievementProgressStatus],
        user_roles=[e.value for e in Role],
    )


def _all_districts():
    """Global district list — thin wrapper around a $distinct."""
    from config.config import settings
    from config.db import get_db
    vals = get_db()[settings.STORES_COLL].distinct(
        "district", {"district": {"$nin": [None, ""]}}
    )
    return [v for v in vals if v]
