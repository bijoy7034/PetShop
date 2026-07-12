from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OFFICE_STAFF = "office_staff"
    SALES_REP = "sales_rep"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


ALL_ROLES: frozenset[str] = frozenset(r.value for r in Role)
