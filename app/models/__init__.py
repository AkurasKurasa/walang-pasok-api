"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`."""

from app.models.base import Base
from app.models.location import Location, LocationType
from app.models.source import SourceKind, SuspensionSource
from app.models.subscription import Channel, Subscription
from app.models.watched_page import WatchedPage
from app.models.suspension import (
    Level,
    Mode,
    Proof,
    Reason,
    SchoolType,
    Status,
    Suspension,
    SuspensionLevel,
    TimeScope,
)

__all__ = [
    "Base",
    "Channel",
    "Level",
    "Location",
    "LocationType",
    "Mode",
    "Proof",
    "Reason",
    "SchoolType",
    "SourceKind",
    "Status",
    "Subscription",
    "Suspension",
    "SuspensionLevel",
    "SuspensionSource",
    "TimeScope",
    "WatchedPage",
]
