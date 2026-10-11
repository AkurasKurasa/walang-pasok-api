from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, enum_column
from app.models.location import Location
from app.models.source import SourceKind, SuspensionSource, infer_kind


class Level(StrEnum):
    PRESCHOOL = "preschool"
    ELEMENTARY = "elementary"
    JUNIOR_HIGH = "junior_high"
    SENIOR_HIGH = "senior_high"
    COLLEGE = "college"
    ALL = "all"


class SchoolType(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    BOTH = "both"


class Mode(StrEnum):
    ALL_CLASSES = "all_classes"
    FACE_TO_FACE_ONLY = "face_to_face_only"


class TimeScope(StrEnum):
    WHOLE_DAY = "whole_day"
    MORNING = "morning"
    AFTERNOON = "afternoon"


class Reason(StrEnum):
    WEATHER = "weather"
    HOLIDAY = "holiday"
    EVENT = "event"
    OTHER = "other"


class Status(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"


class Suspension(Base):
    """One announcement suspending classes in one location.

    `date` is the first affected day. With `until_lifted`, the suspension also
    covers every following day until `lifted_on` (the day classes resume) is set.
    """

    __tablename__ = "suspensions"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    school_type: Mapped[SchoolType] = mapped_column(enum_column(SchoolType), default=SchoolType.BOTH)
    mode: Mapped[Mode] = mapped_column(enum_column(Mode), default=Mode.ALL_CLASSES)
    time_scope: Mapped[TimeScope] = mapped_column(
        enum_column(TimeScope), default=TimeScope.WHOLE_DAY
    )
    until_lifted: Mapped[bool] = mapped_column(Boolean, default=False)
    lifted_on: Mapped[dt.date | None] = mapped_column(Date)
    reason: Mapped[Reason] = mapped_column(enum_column(Reason))
    reason_detail: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(500))
    source_name: Mapped[str] = mapped_column(String(120))
    # Optional details about the primary source, shown as proof on the website.
    source_kind: Mapped[SourceKind | None] = mapped_column(enum_column(SourceKind))
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    source_posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[Status] = mapped_column(enum_column(Status), default=Status.PENDING, index=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    location: Mapped[Location] = relationship(lazy="joined")
    level_rows: Mapped[list[SuspensionLevel]] = relationship(
        back_populates="suspension",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    extra_sources: Mapped[list[SuspensionSource]] = relationship(
        back_populates="suspension",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SuspensionSource.id",
    )

    @property
    def proof(self) -> list["Proof"]:
        """Every source for this suspension, the primary (official) one first."""
        primary = Proof(
            kind=self.source_kind or infer_kind(self.source_url),
            name=self.source_name,
            url=self.source_url,
            excerpt=self.source_excerpt,
            posted_at=self.source_posted_at,
            primary=True,
        )
        extras = [
            Proof(kind=s.kind, name=s.name, url=s.url, excerpt=s.excerpt,
                  posted_at=s.posted_at, primary=False)
            for s in self.extra_sources
        ]
        return [primary, *extras]

    @property
    def levels(self) -> list[Level]:
        """Affected levels in school order (preschool first), not alphabetical."""
        order = list(Level)
        return sorted((row.level for row in self.level_rows), key=order.index)

    @levels.setter
    def levels(self, values: list[Level]) -> None:
        self.level_rows = [SuspensionLevel(level=value) for value in values]

    def __repr__(self) -> str:
        return f"<Suspension {self.id} {self.date} location={self.location_id} {self.status}>"


@dataclass(frozen=True)
class Proof:
    kind: SourceKind
    name: str
    url: str
    excerpt: str | None
    posted_at: dt.datetime | None
    primary: bool


class SuspensionLevel(Base):
    """Education level affected by a suspension (one row per level)."""

    __tablename__ = "suspension_levels"
    __table_args__ = (UniqueConstraint("suspension_id", "level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    suspension_id: Mapped[int] = mapped_column(
        ForeignKey("suspensions.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[Level] = mapped_column(enum_column(Level), index=True)

    suspension: Mapped[Suspension] = relationship(back_populates="level_rows")
