from datetime import datetime, timezone


def now_utc():
    return datetime.now(timezone.utc)


def today_iso_utc():
    """YYYY-MM-DD string in UTC — used as the visit-per-day discriminator so
    a Mongo unique index can enforce 'one visit per store per day'."""
    return now_utc().date().isoformat()
