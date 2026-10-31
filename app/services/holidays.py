"""National holidays, from the yearly presidential proclamation.

Each year has a file in `app/data/holidays/<year>.json`, transcribed from the
proclamation (2026: Proclamation No. 1006, s. 2025). `sync` turns every regular
holiday and special (non-working) day into a verified, nationwide suspension
for the "Philippines" location, which sits above every region, so /check, the
place pages and the JSON API all show it with no extra matching logic.

Special *working* days (e.g. the EDSA anniversary) are in the file for
completeness but are skipped: offices and schools stay open.

Eid'l Fitr and Eid'l Adha are proclaimed separately once their dates are known;
add them to the year's file (or through the Admin workflow) when they are.
"""

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Level, Location, Reason, SourceKind, Suspension
from app.schemas.suspension import SuspensionCreate
from app.services import suspensions as suspension_service
from app.services.psgc import COUNTRY_SLUG

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "holidays"

# Holiday type -> how it's described, or None if classes go on.
TYPES: dict[str, str | None] = {
    "regular": "regular holiday",
    "special_non_working": "special non-working day",
    "special_working": None,
}


class HolidayDataError(ValueError):
    """A holiday file is malformed; the message names the file and entry."""


@dataclass(frozen=True)
class Holiday:
    date: dt.date
    name: str
    type: str
    source_name: str
    source_url: str

    @property
    def no_classes(self) -> bool:
        return TYPES[self.type] is not None

    @property
    def detail(self) -> str:
        return f"{self.name} ({TYPES[self.type]})"

    @property
    def excerpt(self) -> str:
        """The entry as the proclamation lists it, e.g. "All Souls' Day - 2 November (Monday)"."""
        return f"{self.name} - {self.date.day} {self.date:%B} ({self.date:%A})"


def load(path: Path) -> list[Holiday]:
    """Read and validate one year's file. Fails loudly on anything unexpected."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HolidayDataError(f"{path.name}: can't read it: {exc}") from exc
    try:
        year, source = int(data["year"]), data["source"]
        source_name, source_url = str(source["name"]).strip(), str(source["url"]).strip()
        entries = data["holidays"]
    except (KeyError, TypeError, ValueError) as exc:
        raise HolidayDataError(f"{path.name}: needs year, source.name, source.url and holidays") from exc
    if path.stem != str(year):
        raise HolidayDataError(f"{path.name}: says year {year}; the file name must match")
    if not source_name or urlsplit(source_url).scheme not in ("http", "https"):
        raise HolidayDataError(f"{path.name}: source needs a name and an http(s) url")

    holidays: list[Holiday] = []
    seen: set[dt.date] = set()
    for i, entry in enumerate(entries):
        where = f"{path.name} entry {i + 1}"
        try:
            day = dt.date.fromisoformat(entry["date"])
            name, kind = str(entry["name"]).strip(), entry["type"]
        except (KeyError, TypeError, ValueError) as exc:
            raise HolidayDataError(f"{where}: needs a YYYY-MM-DD date, a name and a type") from exc
        if day.year != year:
            raise HolidayDataError(f"{where}: {day} isn't in {year}")
        if kind not in TYPES:
            raise HolidayDataError(f"{where}: type must be one of {sorted(TYPES)}, not {kind!r}")
        if not name:
            raise HolidayDataError(f"{where}: name is empty")
        if day in seen:
            raise HolidayDataError(f"{where}: {day} is listed twice")
        seen.add(day)
        holidays.append(Holiday(day, name, kind, source_name, source_url))
    return sorted(holidays, key=lambda h: h.date)


def load_all(data_dir: Path = DATA_DIR) -> list[Holiday]:
    return [h for path in sorted(data_dir.glob("*.json")) for h in load(path)]


def years_available(data_dir: Path = DATA_DIR) -> set[int]:
    return {int(p.stem) for p in data_dir.glob("*.json") if p.stem.isdigit()}


def missing_next_year(today: dt.date, data_dir: Path = DATA_DIR) -> int | None:
    """Next year, if it's October or later and its holiday file hasn't been added yet.

    The proclamation usually comes out around September.
    """
    upcoming = today.year + 1
    return upcoming if today.month >= 10 and upcoming not in years_available(data_dir) else None


@dataclass
class SyncReport:
    created: list[Holiday] = field(default_factory=list)
    already_listed: int = 0
    working_days_skipped: int = 0


def sync(db: Session, holidays: list[Holiday]) -> SyncReport:
    """Make each no-classes holiday a verified nationwide suspension. Safe to repeat."""
    country = db.scalar(select(Location).where(Location.slug == COUNTRY_SLUG))
    if country is None:
        raise RuntimeError("The 'philippines' location is missing. Load the places first: "
                           "python -m scripts.seed --locations-only")
    report = SyncReport()
    for h in holidays:
        if not h.no_classes:
            report.working_days_skipped += 1
            continue
        exists = db.scalar(select(Suspension.id).where(
            Suspension.location_id == country.id,
            Suspension.date == h.date,
            Suspension.reason == Reason.HOLIDAY,
            Suspension.source_name == h.source_name,
        ))
        if exists:
            report.already_listed += 1
            continue
        created = suspension_service.create(db, SuspensionCreate(
            location=COUNTRY_SLUG, date=h.date, levels=[Level.ALL], reason=Reason.HOLIDAY,
            reason_detail=h.detail, source_url=h.source_url, source_name=h.source_name,
            source_kind=SourceKind.OTHER, source_excerpt=h.excerpt,
        ))
        suspension_service.verify(db, created.id)
        report.created.append(h)
    return report
