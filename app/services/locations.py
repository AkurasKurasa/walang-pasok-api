from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Location, LocationType

# Guards against a corrupted parent chain looping forever.
_MAX_HIERARCHY_DEPTH = 5


def get_by_slug(db: Session, slug: str) -> Location | None:
    return db.scalar(select(Location).where(Location.slug == slug.strip().lower()))


def search(db: Session, term: str | None, offset: int, limit: int) -> tuple[list[Location], int]:
    """Case-insensitive substring search over name, slug and province."""
    conditions = []
    if term and term.strip():
        escaped = term.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        conditions.append(
            or_(
                Location.name.ilike(pattern, escape="\\"),
                Location.slug.ilike(pattern, escape="\\"),
                Location.province.ilike(pattern, escape="\\"),
            )
        )

    total = db.scalar(select(func.count(Location.id)).where(*conditions)) or 0
    items = db.scalars(
        select(Location).where(*conditions).order_by(Location.name).offset(offset).limit(limit)
    ).all()
    return list(items), total


def coverage_ids(location: Location) -> list[int]:
    """IDs whose suspensions apply to `location`: itself plus its ancestors.

    E.g. San Fernando -> [San Fernando, Pampanga, Region III].
    """
    ids: list[int] = []
    current: Location | None = location
    while current is not None and len(ids) < _MAX_HIERARCHY_DEPTH:
        ids.append(current.id)
        current = current.parent
    return ids


def list_localities(db: Session) -> list[Location]:
    """Cities and municipalities, for the website's search suggestions."""
    return list(
        db.scalars(
            select(Location)
            .where(Location.type.in_([LocationType.CITY, LocationType.MUNICIPALITY]))
            .order_by(Location.name)
        ).all()
    )
