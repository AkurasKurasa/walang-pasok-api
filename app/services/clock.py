"""The single source of "today". Suspensions are announced on Philippine time,
so a server running in UTC must not decide what "today" means on its own."""

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import get_settings


def now() -> dt.datetime:
    return dt.datetime.now(ZoneInfo(get_settings().timezone))


def today() -> dt.date:
    return now().date()


def to_utc(value: dt.datetime | None) -> dt.datetime | None:
    """Store times in UTC. SQLite drops the offset, so a Manila time saved as-is
    would be read back 8 hours off; UTC is unambiguous everywhere."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(get_settings().timezone))
    return value.astimezone(dt.UTC)
