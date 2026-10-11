"""The watcher on a snapshot of what Inquirer actually served on 2026-10-11:
its walang-pasok RSS feed plus the 8 newest articles (tests/fixtures/
inquirer_live_2026-10-11.json). If a site redesign breaks reading, refresh
the snapshot and these tests show what changed.
"""

import datetime as dt
import json
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from app.models import Mode, TimeScope
from app.services import watcher
from scripts.seed import upsert_locations

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "inquirer_live_2026-10-11.json").read_text(encoding="utf-8"))


def live_web():
    items = "".join(
        f"<item><title>{escape(i['title'])}</title><link>{i['link']}</link><pubDate>{i['pub']}</pubDate></item>"
        for i in SNAPSHOT["items"]
    )
    pages = {
        watcher.SOURCES[0].index_url: f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>',
        **SNAPSHOT["pages"],
    }

    def fetch(url):
        if url not in pages:
            raise watcher.FetchError(url)
        return pages[url]

    return fetch


@pytest.fixture
def report(db):
    upsert_locations(db)
    # As if it were Sept. 1, so every article in the feed counts as upcoming.
    return watcher.run_once(db, live_web(), today=dt.date(2026, 9, 1))


def found(report, article_id):
    a = next(a for a in report.articles if f"/{article_id}/" in a.url)
    return a, {c.location.slug: c for c in a.items}


def test_sept_9_metro_manila_and_pampanga(report):
    a, places = found(report, 2301512)

    assert a.date == dt.date(2026, 9, 9)
    assert set(places) == {
        "quezon-city", "caloocan-city", "malabon-city", "mandaluyong-city", "manila", "marikina-city",
        "san-juan-city", "valenzuela-city", "pasay-city",
        "san-simon-pampanga", "santo-tomas-pampanga", "angeles-city", "masantol",
    }
    assert places["manila"].mode is Mode.FACE_TO_FACE_ONLY  # "no face to face classes"
    assert places["pasay-city"].time_scope is TimeScope.AFTERNOON
    assert places["masantol"].lifted_on == dt.date(2026, 9, 11)  # until Sept. 10
    # Pangasinan and Benguet towns aren't in the seed list; they're reported, not guessed.
    assert any(u.startswith("Mangaldan") for u in a.unmatched)


def test_sept_14_partial_town_goes_to_a_person(report):
    a, places = found(report, 2304438)

    assert list(places) == ["santo-tomas-pampanga"]
    assert places["santo-tomas-pampanga"].lifted_on == dt.date(2026, 9, 20)  # until Sept. 19
    assert any(u.startswith("San Simon, Pampanga") and "only some schools" in u for u in a.unmatched)


def test_oct_5_corrected_list(report):
    a, places = found(report, 2316727)

    assert list(places) == ["santa-rita-pampanga"]  # Angeles City was removed by an erratum
    assert places["santa-rita-pampanga"].mode is Mode.FACE_TO_FACE_ONLY


def test_prose_and_school_lists_create_nothing(report):
    for article_id in (2320427, 2317693, 2313097, 2309211):  # prose, prose, universities, Cebu
        a = next(a for a in report.articles if f"/{article_id}/" in a.url)
        assert a.items == [], a.title


def test_everything_is_pending(report, db):
    from app.models import Status, Suspension

    assert db.query(Suspension).count() == 18  # 13 + 3 + 1 + 1
    assert {s.status for s in db.query(Suspension)} == {Status.PENDING}
