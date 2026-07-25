from enum import StrEnum


class NotificationType(StrEnum):
    # Achievement lifecycle
    ACHIEVEMENT_COMPLETED = "achievement.completed"    # rep hit the target
    ACHIEVEMENT_CLAIMED = "achievement.claimed"        # rep claimed → notifies office/admin
    ACHIEVEMENT_REDEEMED = "achievement.redeemed"      # office/admin redeemed → notifies rep

    # Order lifecycle
    ORDER_PLACED = "order.placed"                      # rep placed → office/admin
    ORDER_PENDING_APPROVAL = "order.pending_approval"  # over-credit → admin only
    ORDER_APPROVED = "order.approved"                  # office/admin accepted → rep
    ORDER_REJECTED = "order.rejected"                  # admin rejected → rep
    ORDER_PACKED = "order.packed"                      # office marked packing → rep
    ORDER_DISPATCHED = "order.dispatched"              # out for delivery → rep
    ORDER_DELIVERED = "order.delivered"                # delivered → rep + admin
    ORDER_WAITING_FOR_STOCK = "order.waiting_for_stock"  # order held pending stock arrival
    ORDER_READY_TO_SUBMIT = "order.ready_to_submit"    # stock arrived; rep needs to confirm

    # Credit / payments
    PAYMENT_COLLECTED = "credit.payment_collected"     # payment recorded → rep + office
    PAYMENT_OVERDUE = "credit.payment_overdue"         # scheduler → office/admin
    CREDIT_LIMIT_EXCEEDED = "credit.limit_exceeded"    # order lands in pending_admin → admin

    # Inventory
    LOW_STOCK = "inventory.low_stock"                  # variant dropped below reorder_level
    OUT_OF_STOCK = "inventory.out_of_stock"            # on-hand hit zero
    NEW_PRODUCT_ADDED = "inventory.new_product"        # office added a product → all reps

    # General / broadcast
    ANNOUNCEMENT = "general.announcement"
    SYSTEM_MAINTENANCE = "general.system_maintenance"
    PROFILE_ATTENTION = "general.profile_attention"    # e.g. must_change_password


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
