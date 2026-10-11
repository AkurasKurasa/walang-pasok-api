"""Seed locations (Pampanga + Metro Manila) and real example suspensions.

    python -m scripts.seed             # upsert locations; import the real examples
    python -m scripts.seed --samples   # also add fictional samples dated today/tomorrow
    python -m scripts.seed --reset     # delete ALL suspensions first
    python -m scripts.seed --locations-only   # just the places (safe to repeat)

Real examples come from real news articles and an LGU announcement (see
scripts/real_examples.py), are run through the same importer the watcher uses,
and are verified. They're in the past, so browse them on the website with
?date=, e.g. /?date=2025-07-21&q=pasig-city.

Fictional samples are dated relative to today (Asia/Manila) so the homepage has
something to show, and point to example.org.

PSGC codes use the 10-digit format. Verify against the PSA's latest PSGC
publication (https://psa.gov.ph/classification/psgc) before production use.
"""

import argparse
import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    Level,
    Location,
    LocationType,
    Mode,
    Reason,
    SchoolType,
    SourceKind,
    Status,
    Suspension,
    SuspensionLevel,
    SuspensionSource,
    TimeScope,
)
from app.schemas.suspension import SuspensionCreate
from app.services import clock, importer
from app.services import suspensions as suspension_service
from scripts.real_examples import ANNOUNCEMENTS, ARTICLES

REGION_III = "Central Luzon (Region III)"
NCR = "National Capital Region (NCR)"


@dataclass(frozen=True)
class Loc:
    psgc_code: str
    name: str
    slug: str
    type: LocationType
    region: str
    province: str | None
    parent_slug: str | None


R, P, C, M = LocationType.REGION, LocationType.PROVINCE, LocationType.CITY, LocationType.MUNICIPALITY


def _pampanga_lgu(code: str, name: str, slug: str, type_: LocationType = M) -> Loc:
    return Loc(f"03054{code}000", name, slug, type_, REGION_III, "Pampanga", "pampanga")


def _ncr_lgu(code: str, name: str, slug: str, type_: LocationType = C) -> Loc:
    return Loc(code, name, slug, type_, NCR, None, "metro-manila")


# Parents must come before children.
LOCATIONS: list[Loc] = [
    Loc("0300000000", REGION_III, "central-luzon", R, REGION_III, None, None),
    Loc("0305400000", "Pampanga", "pampanga", P, REGION_III, "Pampanga", "central-luzon"),
    # Angeles is a highly urbanized city: independent of the province, so its
    # parent is the region (a Pampanga-wide suspension does not cover it).
    Loc("0330100000", "Angeles City", "angeles-city", C, REGION_III, "Pampanga", "central-luzon"),
    _pampanga_lgu("16", "City of San Fernando", "san-fernando-pampanga", C),
    _pampanga_lgu("09", "Mabalacat City", "mabalacat-city", C),
    _pampanga_lgu("02", "Apalit", "apalit"),
    _pampanga_lgu("03", "Arayat", "arayat"),
    _pampanga_lgu("04", "Bacolor", "bacolor"),
    _pampanga_lgu("05", "Candaba", "candaba"),
    _pampanga_lgu("06", "Floridablanca", "floridablanca"),
    _pampanga_lgu("07", "Guagua", "guagua"),
    _pampanga_lgu("08", "Lubao", "lubao"),
    _pampanga_lgu("10", "Macabebe", "macabebe"),
    _pampanga_lgu("11", "Magalang", "magalang"),
    _pampanga_lgu("12", "Masantol", "masantol"),
    _pampanga_lgu("13", "Mexico", "mexico-pampanga"),
    _pampanga_lgu("14", "Minalin", "minalin"),
    _pampanga_lgu("15", "Porac", "porac"),
    _pampanga_lgu("17", "San Luis", "san-luis-pampanga"),
    _pampanga_lgu("18", "San Simon", "san-simon-pampanga"),
    _pampanga_lgu("19", "Santa Ana", "santa-ana-pampanga"),
    _pampanga_lgu("20", "Santa Rita", "santa-rita-pampanga"),
    _pampanga_lgu("21", "Santo Tomas", "santo-tomas-pampanga"),
    _pampanga_lgu("22", "Sasmuan", "sasmuan"),
    Loc("1300000000", NCR, "metro-manila", R, NCR, None, None),
    _ncr_lgu("1380100000", "Caloocan City", "caloocan-city"),
    _ncr_lgu("1380200000", "Las Piñas City", "las-pinas-city"),
    _ncr_lgu("1380300000", "Makati City", "makati-city"),
    _ncr_lgu("1380400000", "Malabon City", "malabon-city"),
    _ncr_lgu("1380500000", "Mandaluyong City", "mandaluyong-city"),
    _ncr_lgu("1380600000", "City of Manila", "manila"),
    _ncr_lgu("1380700000", "Marikina City", "marikina-city"),
    _ncr_lgu("1380800000", "Muntinlupa City", "muntinlupa-city"),
    _ncr_lgu("1380900000", "Navotas City", "navotas-city"),
    _ncr_lgu("1381000000", "Parañaque City", "paranaque-city"),
    _ncr_lgu("1381100000", "Pasay City", "pasay-city"),
    _ncr_lgu("1381200000", "Pasig City", "pasig-city"),
    _ncr_lgu("1381300000", "Quezon City", "quezon-city"),
    _ncr_lgu("1381400000", "San Juan City", "san-juan-city"),
    _ncr_lgu("1381500000", "Taguig City", "taguig-city"),
    _ncr_lgu("1381600000", "Valenzuela City", "valenzuela-city"),
    _ncr_lgu("1381701000", "Pateros", "pateros", M),
]


def upsert_locations(db: Session) -> int:
    by_slug: dict[str, Location] = {}
    for data in LOCATIONS:
        loc = db.scalar(select(Location).where(Location.psgc_code == data.psgc_code))
        if loc is None:
            loc = Location(psgc_code=data.psgc_code)
            db.add(loc)
        loc.name, loc.slug, loc.type = data.name, data.slug, data.type
        loc.region, loc.province = data.region, data.province
        loc.parent = by_slug[data.parent_slug] if data.parent_slug else None
        by_slug[data.slug] = loc
    db.commit()
    return len(by_slug)


def sample_suspensions(db: Session, today: dt.date) -> list[Suspension]:
    loc = {slug: db.scalar(select(Location).where(Location.slug == slug)) for slug in (
        "angeles-city", "pampanga", "san-fernando-pampanga", "quezon-city",
        "marikina-city", "manila", "metro-manila",
    )}
    verified_at = clock.now()
    k_to_12 = [Level.PRESCHOOL, Level.ELEMENTARY, Level.JUNIOR_HIGH, Level.SENIOR_HIGH]

    def make(slug: str, day: dt.date, levels: list[Level], reason: Reason, detail: str,
             source: str, status: Status = Status.VERIFIED, excerpt: str | None = None,
             news: bool = False, **extra) -> Suspension:
        # Announcements usually drop the evening before, around 8 PM Manila time.
        posted = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(12, 5), tzinfo=dt.UTC)
        s = Suspension(
            location=loc[slug], date=day, reason=reason, reason_detail=detail,
            source_name=f"{source} (sample)",
            source_url=f"https://example.org/walang-pasok/{slug}/{day.isoformat()}",
            source_kind=SourceKind.FACEBOOK_POST,
            source_excerpt=f"[Sample announcement] {excerpt}" if excerpt else None,
            source_posted_at=posted,
            status=status, verified_at=verified_at if status is Status.VERIFIED else None,
            **extra,
        )
        s.levels = levels
        if news:
            s.extra_sources = [SuspensionSource(
                kind=SourceKind.NEWS_ARTICLE, name="Example News (sample)",
                url=f"https://example.org/news/walang-pasok-{day.isoformat()}",
                excerpt="[Sample article] Several LGUs suspended classes due to the storm.",
                posted_at=posted + dt.timedelta(minutes=40),
            )]
        return s

    tomorrow = today + dt.timedelta(days=1)
    return [
        make("angeles-city", tomorrow, [Level.ALL], Reason.WEATHER,
             "Tropical Storm Signal No. 2", "Angeles City Information Office",
             excerpt="WALANG PASOK: Classes at all levels, public and private, are suspended "
                     "tomorrow due to Tropical Storm Signal No. 2. Stay safe, Angeleños!",
             news=True),
        make("pampanga", tomorrow, k_to_12, Reason.WEATHER,
             "Province-wide suspension, preschool to senior high", "Pampanga Provincial Government",
             excerpt="Classes from preschool to senior high school in all public and private "
                     "schools in the province are suspended tomorrow.", news=True),
        make("san-fernando-pampanga", today, [Level.ELEMENTARY, Level.JUNIOR_HIGH], Reason.EVENT,
             "City fiesta", "City of San Fernando PIO", school_type=SchoolType.PUBLIC,
             excerpt="No classes in public elementary and junior high schools for the city fiesta."),
        make("quezon-city", today - dt.timedelta(days=2), [Level.ALL], Reason.WEATHER,
             "Flooding in several barangays; online classes continue", "Quezon City Government",
             until_lifted=True, mode=Mode.FACE_TO_FACE_ONLY,
             excerpt="Face-to-face classes remain suspended until further notice. "
                     "Online and modular classes continue.", news=True),
        make("marikina-city", today, [Level.ALL], Reason.WEATHER,
             "Marikina River at second alarm", "Marikina PIO", time_scope=TimeScope.AFTERNOON,
             excerpt="Afternoon classes at all levels are suspended as the Marikina River "
                     "reaches the second alarm."),
        make("metro-manila", today + dt.timedelta(days=7), [Level.ALL], Reason.HOLIDAY,
             "Special non-working day", "Presidential Communications Office"),
        make("manila", tomorrow, [Level.COLLEGE], Reason.EVENT,
             "Unconfirmed report, awaiting verification", "Social media post",
             status=Status.PENDING),
    ]


def import_real_examples(db: Session) -> tuple[int, int]:
    """Import and verify the real examples. Safe to re-run: already-listed sources are skipped."""
    index = importer.PlaceIndex.from_db(db)
    created = proof = 0
    for a in ARTICLES:
        mode = importer.article_mode("", "\n".join(a.text.splitlines()[:3]))
        result = importer.parse_text(index, a.text, a.date, a.reason, mode)
        importer.import_parsed(db, result, a.date, importer.SourceInfo(
            url=a.url, name=a.source_name, kind=a.kind, posted_at=a.posted_at, reason_detail=a.reason_detail,
        ))
        for c in result.candidates:
            if c.action == "created":
                created += 1
                suspension_service.verify(db, c.suspension_id)
            elif c.action == "added_source":
                proof += 1

    for n in ANNOUNCEMENTS:
        loc = db.scalar(select(Location).where(Location.slug == n.location_slug))
        exists = db.scalar(select(Suspension).where(
            Suspension.location_id == loc.id, Suspension.date == n.date, Suspension.source_url == n.url,
        ))
        if exists:
            continue
        s = suspension_service.create(db, SuspensionCreate(
            location=n.location_slug, date=n.date, levels=[Level.ALL], reason=n.reason,
            reason_detail=n.reason_detail, source_url=n.url, source_name=n.source_name,
            source_kind=n.kind, source_excerpt=n.excerpt, source_posted_at=n.posted_at,
            time_scope=TimeScope.AFTERNOON if n.afternoon_only else TimeScope.WHOLE_DAY,
        ))
        suspension_service.verify(db, s.id)
        created += 1
    return created, proof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete all suspensions first")
    parser.add_argument("--samples", action="store_true", help="also add fictional samples dated today/tomorrow")
    parser.add_argument("--locations-only", action="store_true",
                        help="only add/update the places (what the watcher runs before each check)")
    args = parser.parse_args()

    with SessionLocal() as db:
        print(f"Locations upserted: {upsert_locations(db)}")
        if args.locations_only:
            return

        if args.reset:
            db.execute(delete(SuspensionSource))
            db.execute(delete(SuspensionLevel))
            db.execute(delete(Suspension))
            db.commit()

        created, proof = import_real_examples(db)
        print(f"Real examples: {created} suspensions added, {proof} extra sources attached.")
        print("  Try: /?date=2025-07-21&q=pasig-city  or  /?date=2025-10-20&q=angeles-city")

        if args.samples:
            samples = sample_suspensions(db, clock.today())
            db.add_all(samples)
            db.commit()
            print(f"Fictional samples added: {len(samples)} (relative to {clock.today()})")


if __name__ == "__main__":
    main()
