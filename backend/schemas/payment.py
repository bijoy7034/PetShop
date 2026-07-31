from pydantic import BaseModel


class PaymentSummary(BaseModel):
    """Payment collections rollup for a calendar month.

    payment_methods_breakdown keys are lowercased method strings taken
    verbatim from the recorded payments (e.g. 'upi', 'bank_transfer',
    'cheque', 'cash', 'unspecified' for payments with no method set).
    Values are total collected in that method within the month.
    """
    period: str
    total_collected_this_month: float
    today_collected_amount: float
    pending_collection_amount: float
    collections_count_today: int
    payment_methods_breakdown: dict[str, float]
