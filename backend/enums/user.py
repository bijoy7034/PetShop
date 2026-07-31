from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OFFICE_STAFF = "office_staff"
    SALES_REP = "sales_rep"
    # Runs the developer console: reads all read-only routes for
    # troubleshooting, writes only to /settings and /notifications/send.
    # Not intended for day-to-day operations.
    DEVELOPER = "developer"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


ALL_ROLES: frozenset[str] = frozenset(r.value for r in Role)
