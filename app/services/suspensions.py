"""Suspension business logic: filtering, the /check answer, create and verify.

Kept free of FastAPI so it can be reused by future callers (an AI announcement
parser, webhook dispatchers, background jobs).
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session, contains_eager

from app.errors import BadRequestError, NotFoundError
from app.models import (
    Level,
    Location,
    SchoolType,
    Status,
    Suspension,
    SuspensionLevel,
    SuspensionSource,
)
from app.models.source import infer_kind
from app.schemas.suspension import SourceCreate, SuspensionCreate
from app.services import clock, locations


@dataclass(frozen=True)
class SuspensionFilter:
    """Criteria for listing suspensions. `None` means "don't filter on this"."""

    on_date: dt.date | None = None
    location_ids: Sequence[int] | None = None
    level: Level | None = None
    school_type: SchoolType | None = None
    status: Status | None = Status.VERIFIED


def covers_date(day: dt.date) -> ColumnElement[bool]:
    """A suspension covers `day` if it is on that day, or it started earlier,
    is "until lifted", and classes had not resumed by `day`."""
    return or_(
        Suspension.date == day,
        and_(
            Suspension.until_lifted.is_(True),
            Suspension.date <= day,
            or_(Suspension.lifted_on.is_(None), Suspension.lifted_on > day),
        ),
    )


def affects_level(level: Level) -> ColumnElement[bool]:
    """A query for one level also matches suspensions declared for "all" levels."""
    levels = [Level.ALL] if level is Level.ALL else [level, Level.ALL]
    return Suspension.level_rows.any(SuspensionLevel.level.in_(levels))


def affects_school_type(school_type: SchoolType) -> ColumnElement[bool]:
    types = [SchoolType.BOTH] if school_type is SchoolType.BOTH else [school_type, SchoolType.BOTH]
    return Suspension.school_type.in_(types)


def _conditions(f: SuspensionFilter) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if f.status is not None:
        conditions.append(Suspension.status == f.status)
    if f.on_date is not None:
        conditions.append(covers_date(f.on_date))
    if f.location_ids is not None:
        conditions.append(Suspension.location_id.in_(f.location_ids))
    if f.level is not None:
        conditions.append(affects_level(f.level))
    if f.school_type is not None:
        conditions.append(affects_school_type(f.school_type))
    return conditions


def list_suspensions(
    db: Session, f: SuspensionFilter, offset: int = 0, limit: int | None = None
) -> tuple[list[Suspension], int]:
    """Matching suspensions (newest first) and the total count before paging."""
    conditions = _conditions(f)
    total = db.scalar(select(func.count(Suspension.id)).where(*conditions)) or 0
    stmt = (
        select(Suspension)
        .join(Suspension.location)
        .options(contains_eager(Suspension.location))
        .where(*conditions)
        .order_by(Suspension.date.desc(), Location.name, Suspension.id)
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt).unique().all()), total


@dataclass
class CheckOutcome:
    suspended: bool
    suspensions: list[Suspension]
    sources: list[tuple[str, str]] = field(default_factory=list)  # (name, url), de-duplicated


def check(
    db: Session,
    location: Location,
    day: dt.date,
    level: Level | None = None,
    school_type: SchoolType | None = None,
) -> CheckOutcome:
    """Are classes suspended in `location` on `day`? Includes suspensions
    declared for a parent area (e.g. province-wide or region-wide)."""
    f = SuspensionFilter(
        on_date=day,
        location_ids=locations.coverage_ids(location),
        level=level,
        school_type=school_type,
    )
    found, _ = list_suspensions(db, f)
    sources = list(dict.fromkeys((s.source_name, s.source_url) for s in found))
    return CheckOutcome(suspended=bool(found), suspensions=found, sources=sources)


def get_public(db: Session, suspension_id: int) -> Suspension:
    suspension = db.get(Suspension, suspension_id)
    if suspension is None or suspension.status is not Status.VERIFIED:
        raise NotFoundError(f"Suspension {suspension_id} not found.")
    return suspension


def create(db: Session, data: SuspensionCreate) -> Suspension:
    location = locations.get_by_slug(db, data.location)
    if location is None:
        raise BadRequestError(f"Unknown location '{data.location}'.", {"field": "location"})

    suspension = Suspension(
        location=location,
        date=data.date,
        school_type=data.school_type,
        mode=data.mode,
        time_scope=data.time_scope,
        until_lifted=data.until_lifted,
        reason=data.reason,
        reason_detail=data.reason_detail,
        source_url=str(data.source_url),
        source_name=data.source_name,
        source_kind=data.source_kind or infer_kind(str(data.source_url)),
        source_excerpt=data.source_excerpt,
        source_posted_at=clock.to_utc(data.source_posted_at),
        status=Status.PENDING,
    )
    suspension.levels = data.levels
    suspension.extra_sources = [_source(s) for s in data.extra_sources]
    db.add(suspension)
    db.commit()
    db.refresh(suspension)
    return suspension


def _source(data: SourceCreate) -> SuspensionSource:
    url = str(data.url)
    return SuspensionSource(
        kind=data.kind or infer_kind(url),
        name=data.name,
        url=url,
        excerpt=data.excerpt,
        posted_at=clock.to_utc(data.posted_at),
    )


def add_source(db: Session, suspension_id: int, data: SourceCreate) -> Suspension:
    """Attach an extra corroborating source (e.g. a news article) to a suspension."""
    suspension = db.get(Suspension, suspension_id)
    if suspension is None:
        raise NotFoundError(f"Suspension {suspension_id} not found.")
    suspension.extra_sources.append(_source(data))
    db.commit()
    db.refresh(suspension)
    return suspension


def verify(db: Session, suspension_id: int) -> Suspension:
    """Mark a suspension verified. Idempotent: re-verifying keeps the first timestamp."""
    return verify_with_change(db, suspension_id)[0]


def verify_with_change(db: Session, suspension_id: int) -> tuple[Suspension, bool]:
    """Like `verify`, but also says whether this call made it public, so alerts
    go out exactly once."""
    suspension = db.get(Suspension, suspension_id)
    if suspension is None:
        raise NotFoundError(f"Suspension {suspension_id} not found.")
    if suspension.status is Status.VERIFIED:
        return suspension, False
    suspension.status = Status.VERIFIED
    suspension.verified_at = clock.now()
    db.commit()
    db.refresh(suspension)
    return suspension, True


def recent_days(db: Session, before: dt.date, limit: int = 4) -> list[tuple[dt.date, int]]:
    """The latest days before `before` with verified suspensions, and how many."""
    rows = db.execute(
        select(Suspension.date, func.count(Suspension.id))
        .where(Suspension.status == Status.VERIFIED, Suspension.date < before)
        .group_by(Suspension.date)
        .order_by(Suspension.date.desc())
        .limit(limit)
    ).all()
    return [(day, count) for day, count in rows]
