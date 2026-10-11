from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, enum_column

if TYPE_CHECKING:
    from app.models.suspension import Suspension


class SourceKind(StrEnum):
    FACEBOOK_POST = "facebook_post"
    X_POST = "x_post"
    NEWS_ARTICLE = "news_article"
    OFFICIAL_WEBSITE = "official_website"
    OTHER = "other"


_FACEBOOK_HOSTS = ("facebook.com", "fb.com", "fb.watch")
_X_HOSTS = ("x.com", "twitter.com")


def infer_kind(url: str) -> SourceKind:
    """Best guess at what a link points to, from its host name."""
    host = (urlparse(url).hostname or "").lower()

    def on(domains: tuple[str, ...]) -> bool:
        return any(host == d or host.endswith("." + d) for d in domains)

    if on(_FACEBOOK_HOSTS):
        return SourceKind.FACEBOOK_POST
    if on(_X_HOSTS):
        return SourceKind.X_POST
    if host.endswith(".gov.ph") or host == "gov.ph":
        return SourceKind.OFFICIAL_WEBSITE
    return SourceKind.OTHER


class SuspensionSource(Base):
    """An extra source that corroborates a suspension, e.g. a news article that
    repeats the LGU's Facebook post. The suspension's own `source_*` fields hold
    the primary (official) source; these are shown alongside it as proof."""

    __tablename__ = "suspension_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    suspension_id: Mapped[int] = mapped_column(
        ForeignKey("suspensions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[SourceKind] = mapped_column(enum_column(SourceKind))
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(500))
    # The announcement text, quoted exactly as posted.
    excerpt: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    suspension: Mapped[Suspension] = relationship(back_populates="extra_sources")
