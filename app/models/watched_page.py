from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WatchedPage(Base):
    """A page the watcher has read, so the next run skips it unless it changed.

    Kept in the database (not a file) because each GitHub Actions run starts
    on a fresh machine with no memory of the last one.
    """

    __tablename__ = "watched_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(300))
    found: Mapped[int] = mapped_column(Integer, default=0)  # suspensions read from it, last time
    last_error: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
