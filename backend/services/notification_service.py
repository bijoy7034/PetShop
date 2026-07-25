"""Domain-level notification helpers.

Route/service code calls these instead of writing directly to the
notifications collection — that keeps the payload consistent, makes
audience selection ("all admins", "one rep") explicit, and gives us a
single spot to swap in push/email later.
"""
from config.config import settings
from config.db import get_db
from enums.notification import NotificationType
from repository.notification_repo import NotificationRepository


def _office_and_admin_ids():
    """Everyone who should see office-facing notifications (achievement
    claims, credit-limit proposals, etc.). Sales reps are excluded."""
    users = get_db()[settings.USERS_COLL].find(
        {"role": {"$in": ["admin", "office_staff"]}, "status": "active"},
        {"_id": 1},
    )
    return [str(u["_id"]) for u in users]


def notify_achievement_completed(*, sales_rep, achievement, progress_id):
    """Fires when a rep's progress crosses its target for the first time."""
    NotificationRepository.create(
        user_id=sales_rep["_id"],
        type=NotificationType.ACHIEVEMENT_COMPLETED,
        title=f"You unlocked '{achievement.get('title')}'",
        body=(
            f"Reward: {(achievement.get('reward') or {}).get('text') or ''}. "
            f"Claim it from your Achievements page."
        ),
        meta={
            "achievement_id": achievement["_id"],
            "progress_id": progress_id,
        },
        link=f"/achievements/mine",
    )


def notify_achievement_claimed(*, sales_rep, achievement, progress_id):
    """Rep claimed — notify every office/admin so someone can redeem it."""
    admins = _office_and_admin_ids()
    if not admins:
        return
    NotificationRepository.bulk_create([
        {
            "user_id": uid,
            "type": NotificationType.ACHIEVEMENT_CLAIMED,
            "title": f"{sales_rep.get('name') or 'A rep'} claimed '{achievement.get('title')}'",
            "body": f"Please hand out the reward and mark it redeemed.",
            "meta": {
                "achievement_id": achievement["_id"],
                "progress_id": progress_id,
                "sales_rep_id": sales_rep["_id"],
                "sales_rep_name": sales_rep.get("name"),
            },
            "link": f"/achievements/{achievement['_id']}/progress?status=claimed",
        }
        for uid in admins
    ])


def notify_achievement_redeemed(*, sales_rep, achievement, progress_id, actor):
    """Reward was handed out — tell the rep so their app updates."""
    NotificationRepository.create(
        user_id=sales_rep["_id"],
        type=NotificationType.ACHIEVEMENT_REDEEMED,
        title=f"'{achievement.get('title')}' has been redeemed",
        body=(
            f"{(actor or {}).get('name') or 'The office'} marked your reward as "
            f"delivered."
        ),
        meta={
            "achievement_id": achievement["_id"],
            "progress_id": progress_id,
        },
        link=f"/achievements/mine",
    )


# --------- Order lifecycle ---------

def _order_meta(order):
    return {
        "order_id": order["_id"],
        "order_code": order.get("code"),
        "store_id": order.get("store_id"),
        "store_name": order.get("store_name"),
        "sales_rep_id": order.get("sales_rep_id"),
        "total": order.get("total"),
    }


def notify_order_placed(*, order):
    """Rep placed an order → office/admin gets a heads-up so someone
    can accept it. If the order landed in pending_admin_approval
    (over-credit), route the notification only to admin, not office."""
    is_pending = order.get("status") == "pending_admin_approval"
    if is_pending:
        _notify_admins_only(
            type=NotificationType.ORDER_PENDING_APPROVAL,
            title=f"Order {order.get('code') or ''} needs admin approval",
            body=(
                f"{order.get('sales_rep_name') or 'A rep'} placed an order at "
                f"'{order.get('store_name')}' that exceeds the store's credit "
                f"limit. Please review."
            ),
            meta=_order_meta(order),
            link=f"/orders/{order['_id']}",
        )
        return
    _notify_office_and_admin(
        type=NotificationType.ORDER_PLACED,
        title=f"New order {order.get('code') or ''} placed",
        body=(
            f"{order.get('sales_rep_name') or 'A rep'} placed an order at "
            f"'{order.get('store_name')}' — total {order.get('total')}."
        ),
        meta=_order_meta(order),
        link=f"/orders/{order['_id']}",
    )


def notify_order_status(*, order, prev_status, actor=None):
    """Fires the right notification for the new status. Called from every
    order-lifecycle endpoint. Only the rep is notified for their own
    order transitions; delivered fires an extra copy to admins so
    ops has a delivery timeline."""
    new_status = order["status"]
    if new_status == prev_status:
        return
    rep_id = order.get("sales_rep_id")
    if not rep_id:
        return

    if new_status == "accepted":
        NotificationRepository.create(
            user_id=rep_id,
            type=NotificationType.ORDER_APPROVED,
            title=f"Your order {order.get('code') or ''} was approved",
            body=f"{(actor or {}).get('name') or 'Office'} accepted the order.",
            meta=_order_meta(order),
            link=f"/orders/{order['_id']}",
        )
    elif new_status == "placed" and prev_status == "pending_admin_approval":
        # Admin approved an over-credit or pending order. Rep sees a
        # slightly different message so they know it's a re-entry to
        # the flow, not a fresh acceptance.
        over_flag = order.get("over_credit_approved")
        body = (
            f"Admin approved despite exceeding credit by "
            f"{order.get('over_credit_amount', 0):.2f}."
            if over_flag
            else "Admin approved your pending order."
        )
        NotificationRepository.create(
            user_id=rep_id,
            type=NotificationType.ORDER_APPROVED,
            title=f"Order {order.get('code') or ''} approved by admin",
            body=body,
            meta={**_order_meta(order),
                  "over_credit_approved": bool(over_flag),
                  "over_credit_amount": order.get("over_credit_amount", 0)},
            link=f"/orders/{order['_id']}",
        )
    elif new_status == "cancelled" and prev_status == "pending_admin_approval":
        NotificationRepository.create(
            user_id=rep_id,
            type=NotificationType.ORDER_REJECTED,
            title=f"Your order {order.get('code') or ''} was rejected",
            body=f"Reason: {order.get('rejection_reason') or 'not stated'}.",
            meta=_order_meta(order),
            link=f"/orders/{order['_id']}",
        )
    elif new_status == "packing":
        NotificationRepository.create(
            user_id=rep_id,
            type=NotificationType.ORDER_PACKED,
            title=f"Order {order.get('code') or ''} is being packed",
            meta=_order_meta(order),
            link=f"/orders/{order['_id']}",
        )
    elif new_status == "out_for_delivery":
        NotificationRepository.create(
            user_id=rep_id,
            type=NotificationType.ORDER_DISPATCHED,
            title=f"Order {order.get('code') or ''} is out for delivery",
            meta=_order_meta(order),
            link=f"/orders/{order['_id']}",
        )
    elif new_status == "delivered":
        NotificationRepository.create(
            user_id=rep_id,
            type=NotificationType.ORDER_DELIVERED,
            title=f"Order {order.get('code') or ''} was delivered",
            meta=_order_meta(order),
            link=f"/orders/{order['_id']}",
        )
        # Admins get a delivered ping too for ops visibility.
        _notify_admins_only(
            type=NotificationType.ORDER_DELIVERED,
            title=f"Order {order.get('code') or ''} delivered to '{order.get('store_name')}'",
            meta=_order_meta(order),
            link=f"/orders/{order['_id']}",
        )


# --------- Credit / payments ---------

def notify_payment_collected(*, order, amount, actor=None):
    """Payment recorded on an order. Notifies both the rep (whose store
    paid) and the office team (accounting log)."""
    rep_id = order.get("sales_rep_id")
    if rep_id:
        NotificationRepository.create(
            user_id=rep_id,
            type=NotificationType.PAYMENT_COLLECTED,
            title=f"Payment of {amount} received on {order.get('code')}",
            body=f"'{order.get('store_name')}' paid {amount}.",
            meta={**_order_meta(order), "amount": amount},
            link=f"/orders/{order['_id']}",
        )
    _notify_office_and_admin(
        type=NotificationType.PAYMENT_COLLECTED,
        title=f"Payment received: {amount} on {order.get('code')}",
        body=(
            f"{(actor or {}).get('name') or 'Someone'} recorded a payment of "
            f"{amount} against order {order.get('code')}."
        ),
        meta={**_order_meta(order), "amount": amount},
        link=f"/orders/{order['_id']}",
    )


def notify_payment_overdue(*, order):
    """Called by the scheduler for every unpaid order past its
    payment_due_date. One row per overdue order per run."""
    _notify_office_and_admin(
        type=NotificationType.PAYMENT_OVERDUE,
        title=f"Overdue: {order.get('code')} at '{order.get('store_name')}'",
        body=(
            f"Outstanding {order.get('outstanding')} was due on "
            f"{order.get('payment_due_date')}."
        ),
        meta={**_order_meta(order),
              "outstanding": order.get("outstanding"),
              "payment_due_date": str(order.get("payment_due_date"))},
        link=f"/orders/{order['_id']}",
    )


# --------- Inventory ---------

def notify_low_stock(*, product_name, variant_label, variant_id,
                     product_id, on_hand, reorder_level):
    """Fires when a stock adjust leaves on_hand at or below reorder_level
    but above zero. Zero triggers OUT_OF_STOCK instead."""
    _notify_office_and_admin(
        type=NotificationType.LOW_STOCK,
        title=f"Low stock: {product_name} ({variant_label or 'default'})",
        body=(
            f"On hand {on_hand} ≤ reorder level {reorder_level}. "
            f"Time to restock."
        ),
        meta={
            "product_id": product_id, "variant_id": variant_id,
            "on_hand": on_hand, "reorder_level": reorder_level,
        },
        link=f"/products/{product_id}",
    )


def check_stock_after_adjust(*, updated, product_name, variant_label,
                              variant_id, product_id):
    """Look at the last stock_history event on `updated` to decide whether
    the adjust just CROSSED a threshold — fires at most one notification.
    Fires nothing if `updated` has no stock_history (adjust without reason)."""
    hist = updated.get("stock_history") or []
    if not hist:
        return
    last = hist[-1]
    prev = int(last.get("previous_stock") or 0)
    new = int(last.get("new_stock") or 0)
    reorder = int(updated.get("reorder_level") or 0)
    if prev > 0 and new == 0:
        notify_out_of_stock(
            product_name=product_name, variant_label=variant_label,
            variant_id=variant_id, product_id=product_id,
        )
    elif reorder > 0 and prev > reorder and new <= reorder and new > 0:
        notify_low_stock(
            product_name=product_name, variant_label=variant_label,
            variant_id=variant_id, product_id=product_id,
            on_hand=new, reorder_level=reorder,
        )


def notify_out_of_stock(*, product_name, variant_label, variant_id, product_id):
    _notify_office_and_admin(
        type=NotificationType.OUT_OF_STOCK,
        title=f"OUT OF STOCK: {product_name} ({variant_label or 'default'})",
        meta={"product_id": product_id, "variant_id": variant_id},
        link=f"/products/{product_id}",
    )


def notify_new_product(*, product):
    """New product added → notify every active sales_rep so they know
    they can start selling it. Office/admin created it themselves so
    they don't need the ping."""
    reps = _sales_rep_ids()
    if not reps:
        return
    title = f"New product: {product.get('name')}"
    body = f"{product.get('code') or ''} is now available in {product.get('category_name') or 'the catalogue'}."
    NotificationRepository.bulk_create([
        {
            "user_id": uid,
            "type": NotificationType.NEW_PRODUCT_ADDED,
            "title": title,
            "body": body,
            "meta": {
                "product_id": product["_id"],
                "product_code": product.get("code"),
            },
            "link": f"/products/{product['_id']}",
        }
        for uid in reps
    ])


# --------- General / broadcast ---------

def broadcast(*, type, title, body=None, meta=None, link=None,
              audience="all"):
    """Broadcast to every active user, or a role-scoped subset.
    audience ∈ {'all', 'admin', 'office', 'sales_rep', 'admin+office'}."""
    ids = _audience_ids(audience)
    if not ids:
        return 0
    return NotificationRepository.bulk_create([
        {"user_id": uid, "type": type, "title": title, "body": body,
         "meta": meta or {}, "link": link}
        for uid in ids
    ])


def notify_profile_attention(*, user_id, title, body=None):
    """Prompt a specific user (e.g. 'must change password')."""
    NotificationRepository.create(
        user_id=user_id,
        type=NotificationType.PROFILE_ATTENTION,
        title=title, body=body, link="/profile",
    )


# --------- Audience helpers ---------

def _notify_office_and_admin(**kwargs):
    ids = _office_and_admin_ids()
    if not ids:
        return
    NotificationRepository.bulk_create([{"user_id": uid, **kwargs} for uid in ids])


def _notify_admins_only(**kwargs):
    ids = _role_ids(["admin"])
    if not ids:
        return
    NotificationRepository.bulk_create([{"user_id": uid, **kwargs} for uid in ids])


def _sales_rep_ids():
    return _role_ids(["sales_rep"])


def _role_ids(roles):
    users = get_db()[settings.USERS_COLL].find(
        {"role": {"$in": list(roles)}, "status": "active"},
        {"_id": 1},
    )
    return [str(u["_id"]) for u in users]


def _audience_ids(audience):
    if audience == "all":
        return _role_ids(["admin", "office_staff", "sales_rep"])
    if audience == "admin+office":
        return _office_and_admin_ids()
    if audience == "admin":
        return _role_ids(["admin"])
    if audience == "office":
        return _role_ids(["office_staff"])
    if audience == "sales_rep":
        return _role_ids(["sales_rep"])
    return []
