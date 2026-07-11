from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    CASHIER = "cashier"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


ALL_ROLES: frozenset[str] = frozenset(r.value for r in Role)
