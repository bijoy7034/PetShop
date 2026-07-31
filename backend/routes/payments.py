from fastapi import APIRouter, Depends, Query

from middleware.auth import require_any_user
from repository.order_repo import OrderRepository
from schemas.payment import PaymentSummary


router = APIRouter(prefix="/payments", tags=["payments"])


def _is_office(user):
    return user["role"] in ("admin", "office_staff")


@router.get("/summary", response_model=PaymentSummary)
async def payment_summary(
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    sales_rep_id: str | None = Query(
        None,
        description="Office/admin only; sales_rep is force-scoped to their own orders.",
    ),
    current=Depends(require_any_user),
):
    """Payment collections rollup for a calendar month. `year`+`month`
    default to the current month. Sales rep is force-scoped to their own
    orders regardless of `sales_rep_id`."""
    user = current["user"]
    effective_rep = sales_rep_id if _is_office(user) else user["_id"]
    return OrderRepository.payment_summary(
        year=year, month=month, sales_rep_id=effective_rep,
    )
