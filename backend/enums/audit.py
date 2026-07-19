from enum import StrEnum


class ResourceType(StrEnum):
    USER = "user"
    AUTH = "auth"
    CATEGORY = "category"
    PRODUCT = "product"
    INVENTORY = "inventory"
    STORE = "store"
    VISIT = "visit"
    ORDER = "order"


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

    # category lifecycle
    CATEGORY_CREATE = "category.create"
    CATEGORY_UPDATE = "category.update"
    CATEGORY_DELETE = "category.delete"
    SUBCATEGORY_CREATE = "subcategory.create"
    SUBCATEGORY_UPDATE = "subcategory.update"
    SUBCATEGORY_DELETE = "subcategory.delete"

    # product lifecycle
    PRODUCT_CREATE = "product.create"
    PRODUCT_UPDATE = "product.update"
    PRODUCT_DELETE = "product.delete"
    PRODUCT_BULK_UPLOAD = "product.bulk_upload"
    VARIANT_CREATE = "product.variant.create"
    VARIANT_UPDATE = "product.variant.update"
    VARIANT_DELETE = "product.variant.delete"
    VARIANT_STOCK_ADJUST = "product.variant.stock_adjust"

    # inventory (per-variant stock ledger)
    INVENTORY_ADJUST = "inventory.adjust"
    INVENTORY_REORDER_SET = "inventory.reorder_set"
    INVENTORY_RESERVE = "inventory.reserve"
    INVENTORY_RELEASE = "inventory.release"
    INVENTORY_COMMIT = "inventory.commit"

    # store lifecycle
    STORE_CREATE = "store.create"
    STORE_UPDATE = "store.update"
    STORE_DELETE = "store.delete"
    STORE_APPROVE = "store.approve"
    STORE_REJECT = "store.reject"
    STORE_ASSIGN = "store.assign"
    STORE_CREDIT_LIMIT_PROPOSE = "store.credit_limit.propose"
    STORE_CREDIT_LIMIT_APPROVE = "store.credit_limit.approve"
    STORE_CREDIT_LIMIT_REJECT = "store.credit_limit.reject"

    # visit (sales rep field visit)
    VISIT_MARK = "visit.mark"

    # order lifecycle
    ORDER_PLACE = "order.place"
    ORDER_PENDING_APPROVAL = "order.pending_approval"
    ORDER_ADMIN_APPROVE = "order.admin_approve"
    ORDER_ADMIN_REJECT = "order.admin_reject"
    ORDER_CANCEL = "order.cancel"
    ORDER_ACCEPT = "order.accept"
    ORDER_LINES_EDIT = "order.lines_edit"
    ORDER_DELAY = "order.delay"
    ORDER_PACK = "order.pack"
    ORDER_DISPATCH = "order.dispatch"
    ORDER_DELIVER = "order.deliver"
    ORDER_PAYMENT_RECORDED = "order.payment_recorded"
