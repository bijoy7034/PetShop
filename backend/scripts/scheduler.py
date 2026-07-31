"""Daily DB → R2 archive scheduler.

Run as a sidecar container / process:
    uv run python -m scripts.scheduler

The loop ticks every 24 h. On each tick:
  1. Scans overdue orders and fires notifications.
  2. Runs a fresh Mongo → R2 archive dump if DB_DUMP_INTERVAL_DAYS
     have passed since the last dump.

Default DB_DUMP_INTERVAL_DAYS is 1 (daily). Set to 7 for weekly, 14
for bi-weekly, etc. Errors on either task are logged and swallowed —
one failed dump doesn't stop the schedule.

Deploy note: if you run multiple replicas of this scheduler, they will
BOTH dump every interval. Run exactly one instance. If you need HA,
add a Mongo-based leader lock (find_one_and_update with an expiring
lease) — the current design is deliberately not clustered so the
scheduler stays a boring, single-process concern.
"""
import signal
import time
from datetime import datetime, timezone

from config.config import settings
from config.db import mongo_manager
from config.logging.logger import logger
from repository.order_repo import OrderRepository
from services import notification_service
from services.db_dump_service import run_dump
from utils.r2_storage import R2NotConfiguredError


_stopped = False


def _handle_signal(signum, frame):
    global _stopped
    logger.info(f"scheduler: received signal {signum}, will exit after current cycle")
    _stopped = True


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_dump():
    try:
        result = run_dump()
        logger.info(
            f"scheduler: dump OK — key={result['key']} "
            f"docs={result['total_docs']} bytes={result['size_bytes']}"
        )
    except R2NotConfiguredError as e:
        logger.error(f"scheduler: {e} — will retry next cycle")
    except Exception:
        logger.exception("scheduler: dump failed")


def _check_overdue_payments():
    """Scan every day. Fires PAYMENT_OVERDUE for orders past their
    payment_due_date; dedupes so a single order won't spam more than
    once a week."""
    try:
        now = datetime.now(timezone.utc)
        orders = OrderRepository.find_overdue_for_notification(now=now)
        if not orders:
            return
        for o in orders:
            notification_service.notify_payment_overdue(order=o)
            OrderRepository.mark_overdue_notified(o["_id"])
        logger.info(f"scheduler: fired {len(orders)} overdue-payment notifications")
    except Exception:
        logger.exception("scheduler: overdue-payment check failed")


def _sleep_interruptible(seconds, step=30):
    """Sleep in small increments so SIGTERM/SIGINT is picked up quickly."""
    elapsed = 0
    while not _stopped and elapsed < seconds:
        time.sleep(min(step, seconds - elapsed))
        elapsed += step


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    mongo_manager.connect()
    dump_interval_seconds = max(1, settings.DB_DUMP_INTERVAL_DAYS) * 86400
    tick_seconds = 86400  # daily tick so overdue-payment scan runs every day
    logger.info(
        f"scheduler started — tick=1d, dump_every={settings.DB_DUMP_INTERVAL_DAYS}d, "
        f"prefix={settings.R2_ARCHIVE_PREFIX}"
    )

    # Boot cycle: run everything once so a freshly-deployed scheduler
    # doesn't wait a full day / week before its first useful action.
    _check_overdue_payments()
    _run_dump()
    last_dump_at = datetime.now(timezone.utc)

    while not _stopped:
        _sleep_interruptible(tick_seconds)
        if _stopped:
            break
        now = datetime.now(timezone.utc)
        logger.info(f"scheduler: tick at {_iso(now)}")
        _check_overdue_payments()
        if (now - last_dump_at).total_seconds() >= dump_interval_seconds:
            _run_dump()
            last_dump_at = now

    logger.info("scheduler: exiting")


if __name__ == "__main__":
    main()
