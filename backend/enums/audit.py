from enum import StrEnum


class ResourceType(StrEnum):
    USER = "user"
    AUTH = "auth"


class AuditAction(StrEnum):
    # auth events
    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILED = "auth.login.failed"
    LOGOUT = "auth.logout"
    LOGOUT_ALL = "auth.logout_all"
    TOKEN_REFRESH = "auth.token.refresh"
    TOKEN_REFRESH_REUSE = "auth.token.refresh_reuse"
    TOKEN_REFRESH_FAILED = "auth.token.refresh_failed"

    # user lifecycle (admin actions)
    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    USER_DEACTIVATE = "user.deactivate"
    USER_REACTIVATE = "user.reactivate"
    USER_PASSWORD_RESET = "user.password_reset"
    USER_DELETE = "user.delete"
