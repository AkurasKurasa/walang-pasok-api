from __future__ import annotations

import datetime as dt
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, enum_column
from app.models.location import Location


class Channel(StrEnum):
    DISCORD_WEBHOOK = "discord_webhook"


class Subscription(Base):
    """Someone who wants to be told when a suspension is verified.

    `location_id` is the place they follow; `None` means nationwide. The
    webhook URL is a secret (anyone holding it can post to that channel), so it
    is never returned by the API. Subscribers manage their alert with
    `manage_token`, shown to them once when they sign up.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[Channel] = mapped_column(enum_column(Channel))
    target: Mapped[str] = mapped_column(String(500))
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str | None] = mapped_column(String(120))
    manage_token: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    location: Mapped[Location | None] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return f"<Subscription {self.id} {self.channel} location={self.location_id}>"
