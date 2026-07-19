from enum import StrEnum


class VisitMode(StrEnum):
    IN_STORE = "in_store"
    REMOTE = "remote"


class VisitOutcome(StrEnum):
    ORDER_PLACED = "order_placed"
    NO_ORDER = "no_order"
