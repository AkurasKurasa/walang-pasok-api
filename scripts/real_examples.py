"""Real class suspensions, from real news articles and an LGU announcement.

Each entry is the article's URL plus the suspension lines it published, copied
from the article (only the lines this project's seed locations cover, plus a
few neighbours so the importer's "unknown place" handling shows up). The seed
script feeds them through the same importer the watcher uses, so they double
as an end-to-end test of it.

Checked against the live pages on 2026-10-11. News pages get corrected after
publishing (Inquirer's Oct. 5, 2026 list dropped an Angeles City line in an
erratum, so it isn't here), so if a line differs from the page now, the page wins.
"""

import datetime as dt
from dataclasses import dataclass

from app.models import Reason, SourceKind

MANILA = dt.timezone(dt.timedelta(hours=8))


@dataclass(frozen=True)
class RealArticle:
    date: dt.date  # the suspended day
    url: str
    source_name: str
    kind: SourceKind
    posted_at: dt.datetime | None
    reason: Reason
    reason_detail: str | None
    text: str  # suspension lines, as published


ARTICLES: list[RealArticle] = [
    RealArticle(
        date=dt.date(2023, 9, 1),
        url="https://newsinfo.inquirer.net/1824929/walangpasok-class-suspensions-on-friday-september-1",
        source_name="Inquirer.net",
        kind=SourceKind.NEWS_ARTICLE,
        posted_at=dt.datetime(2023, 8, 31, 21, 2, tzinfo=MANILA),
        reason=Reason.WEATHER,
        reason_detail="Southwest monsoon enhanced by Severe Tropical Storm Hanna and Super Typhoon Goring",
        text="""\
Metro Manila
All levels, public and private schools
Angeles, Pampanga
All levels, public and private schools
Atok, Benguet
Preschool to high school, public and private schools
Central Luzon
Masantol, Pampanga – all levels (public and private)
Hagonoy, Bulacan – all levels (public and private)
""",
    ),
    RealArticle(
        date=dt.date(2025, 7, 21),
        url="https://www.gmanetwork.com/news/serbisyopubliko/walangpasok/953178/walang-pasok-class-suspensions-for-monday-july-21-2025/story/",
        source_name="GMA News",
        kind=SourceKind.NEWS_ARTICLE,
        posted_at=dt.datetime(2025, 7, 20, 13, 32, tzinfo=MANILA),
        reason=Reason.WEATHER,
        reason_detail="Southwest monsoon (habagat)",
        text="""\
Metro Manila
Caloocan City - all levels, public and private (afternoon classes)
Makati City - all levels, public and private (afternoon classes)
Malabon City - no face-to-face morning classes in some schools
Manila City - all levels, public and private (afternoon classes)
Marikina City - all levels, public and private (afternoon classes)
Pasay City - all levels, public and private (afternoon classes)
Pasig City - no face-to-face classes, kindergarten to senior high school, public and private
Paranaque - all levels, public and private (afternoon classes)
San Juan City - all levels, public and private
Quezon City - preschool to senior high school, public
Taguig City - all levels, public and private (afternoon classes)
Pampanga
Arayat - all levels, public and private
Bacolor - no face-to-face classes, all levels, public and private
Candaba - no face-to-face classes, all levels, public and private
Floridablanca - no face-to-face classes, all levels, public and private
Guagua - no face-to-face classes, all levels, public and private
Lubao - no face-to-face classes, all levels, public and private
Masantol - no face-to-face classes, all levels, public and private (July 21 and 22)
Mexico - no face-to-face classes, all levels, public and private
Porac - no face-to-face classes, all levels, public and private
San Luis - all levels, public and private
San Simon - all levels, public and private
Sta. Ana - all levels, public and private
Sto. Tomas - no face-to-face classes, daycare to senior high school, public and private
Pangasinan
Dagupan City - no face-to-face classes, all levels, public and private
San Fernando - no face-to-face classes, all levels, public and private
""",
    ),
    RealArticle(
        date=dt.date(2025, 9, 22),
        url="https://www.thesummitexpress.com/2025/09/walang-pasok-class-suspensions-monday-september-22-2025.html",
        source_name="The Summit Express",
        kind=SourceKind.NEWS_ARTICLE,
        posted_at=None,
        reason=Reason.WEATHER,
        reason_detail='Super Typhoon Nando (Ragasa) and the enhanced southwest monsoon (habagat)',
        text="""\
Metro Manila
Caloocan City – face-to-face classes in all levels, public and private
Las Piñas City – all levels, public and private
Malabon City – face-to-face classes in all levels, public and private
City of Manila – face-to-face classes in all levels, public and private
Marikina City – face-to-face classes in all levels, public and private
Navotas City – all levels, public and private
Parañaque City – all levels, public and private
Pateros – face-to-face classes in all levels, public and private
""",
    ),
    RealArticle(
        date=dt.date(2025, 10, 20),
        url="https://www.gmanetwork.com/news/serbisyopubliko/walangpasok/962974/walang-pasok-class-suspensions-for-monday-october-20-2025/story/",
        source_name="GMA News",
        kind=SourceKind.NEWS_ARTICLE,
        posted_at=dt.datetime(2025, 10, 19, 18, 8, tzinfo=MANILA),
        reason=Reason.WEATHER,
        reason_detail='Tropical Storm Ramil',
        text="""\
Pampanga
Angeles City - no classes, all levels, public and private
Arayat - no face-to-face classes, all levels, public and private; shift to alternative learning modules
Mexico - no face-to-face classes, all levels, public and private; shift to alternative learning modules
San Fernando - no face-to-face classes, all levels, public and private; shift to alternative learning modules
Tarlac
San Clemente - no face-to-face classes, all levels, public and private; shift to alternative learning modules
""",
    ),
    RealArticle(
        date=dt.date(2026, 9, 10),
        url="https://newsinfo.inquirer.net/2301928/walang-pasok-class-suspensions-on-thursday-sept-10",
        source_name="Inquirer.net",
        kind=SourceKind.NEWS_ARTICLE,
        posted_at=dt.datetime(2026, 9, 9, 8, 45, tzinfo=MANILA),
        reason=Reason.WEATHER,
        reason_detail='Southwest monsoon (habagat)',
        text="""\
Pampanga
San Simon – no face-to-face classes; all levels; public and private (except Concepcion Integrated School); until Friday, Sept. 11
Sto. Tomas – no face-to-face classes; all levels; public and private (until Saturday, Sept. 12)
Masantol – no face-to-face classes; all levels; public and private
Benguet
Kibungan – no face-to-face classes for daycare
""",
    ),
    RealArticle(
        date=dt.date(2026, 10, 5),
        url="https://newsinfo.inquirer.net/2316727/in-person-class-suspensions-on-oct-5-in-line-of-national-teachers-day-celebration",
        source_name="Inquirer.net",
        kind=SourceKind.NEWS_ARTICLE,
        posted_at=dt.datetime(2026, 10, 4, 16, 27, tzinfo=MANILA),
        reason=Reason.EVENT,
        reason_detail="National Teachers' Day",
        text="""\
Face-to-face classes are suspended in several areas on Monday, October 5, for the celebration of National Teachers' Day.
Luzon
Province of Abra: all levels, public and private schools
Sta. Rita, Pampanga: all levels, public and private schools
Victoria, Tarlac: all levels, public and private schools
""",
    ),
]


@dataclass(frozen=True)
class RealAnnouncement:
    """An official announcement written as prose, not a list, entered by hand."""

    date: dt.date
    location_slug: str
    url: str
    source_name: str
    kind: SourceKind
    posted_at: dt.datetime | None
    excerpt: str
    reason: Reason
    reason_detail: str
    afternoon_only: bool = False


ANNOUNCEMENTS: list[RealAnnouncement] = [
    RealAnnouncement(
        date=dt.date(2025, 7, 21),
        location_slug="metro-manila",
        url="https://quezoncity.gov.ph/walang-pasok-class-and-work-suspension-malacanang-memorandum-circular-no-88-july-21-2025/",
        source_name="Quezon City Government",
        kind=SourceKind.OFFICIAL_WEBSITE,
        posted_at=dt.datetime(2025, 7, 21, 13, 23, tzinfo=MANILA),
        excerpt=(
            "… suspendido ang pasok sa lahat ng antas ng paaralan at tanggapan ng gobyerno sa buong "
            "Metro Manila … ngayong Lunes, July 21, 2025, simula 1:00 PM …"
        ),
        reason=Reason.WEATHER,
        reason_detail="Southwest monsoon (habagat); Malacañang Memorandum Circular No. 88",
        afternoon_only=True,
    ),
]
