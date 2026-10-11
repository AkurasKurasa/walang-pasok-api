"""The importer against full text from live news pages, traps included.

The fixtures are the suspension sections of real articles, copied on
2026-10-11 (see the headline in each file). The index is the project's real
seed list of 42 places, so these tests catch false matches as well as misses.
"""

import datetime as dt
from pathlib import Path

import pytest

from app.models import Level, Location, Mode, SchoolType, TimeScope
from app.services import importer
from scripts.seed import LOCATIONS
from scripts.real_examples import ARTICLES

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def index() -> importer.PlaceIndex:
    places = [
        Location(id=i, psgc_code=l.psgc_code, name=l.name, slug=l.slug, type=l.type,
                 region=l.region, province=l.province)
        for i, l in enumerate(LOCATIONS, start=1)
    ]
    return importer.PlaceIndex(places)


def parse(index, name, day):
    return importer.parse_text(index, (FIXTURES / name).read_text(encoding="utf-8"), day)


def by_slug(result):
    return {c.location.slug: c for c in result.candidates}


def test_gma_oct_20_2025(index):
    found = by_slug(parse(index, "gma_2025-10-20.txt", dt.date(2025, 10, 20)))

    assert list(found) == ["angeles-city", "arayat", "mexico-pampanga", "san-fernando-pampanga"]
    assert found["angeles-city"].mode is Mode.ALL_CLASSES
    assert found["san-fernando-pampanga"].mode is Mode.FACE_TO_FACE_ONLY


def test_gma_oct_20_2025_rejects_same_named_towns_elsewhere(index):
    result = parse(index, "gma_2025-10-20.txt", dt.date(2025, 10, 20))
    unmatched = " ".join(result.unmatched)

    # Santo Tomas under "Pangasinan:" is not Santo Tomas, Pampanga.
    assert "santo-tomas-pampanga" not in by_slug(result)
    assert "Santo Tomas - no face-to-face classes, all levels, public" in unmatched
    # "Quezon" is a province heading here, not Quezon City.
    assert "quezon-city" not in by_slug(result)
    assert "Lucena City" in unmatched


def test_summit_sept_22_2025(index):
    result = parse(index, "summit_2025-09-22.txt", dt.date(2025, 9, 22))
    found = by_slug(result)

    assert set(found) == {
        "caloocan-city", "las-pinas-city", "malabon-city", "manila",
        "marikina-city", "navotas-city", "paranaque-city", "pateros",
    }
    assert found["manila"].mode is Mode.FACE_TO_FACE_ONLY
    assert found["navotas-city"].mode is Mode.ALL_CLASSES
    # The storm-signal paragraph lists Pampanga towns but suspends nothing.
    assert not any(c.location.province == "Pampanga" for c in result.candidates)
    # The dateline isn't reported as an unknown place.
    assert not any(line.startswith("MANILA, Philippines") for line in result.unmatched)


@pytest.mark.parametrize("article", ARTICLES, ids=lambda a: f"{a.source_name}-{a.date}")
def test_every_real_example_parses(index, article):
    result = importer.parse_text(index, article.text, article.date, article.reason)

    assert result.candidates, "each real example should produce at least one suspension"
    for c in result.candidates:
        # Quotes come from the article (two-line entries are joined with ": ").
        assert all(part in article.text for part in c.line.split(": ", 1)) or c.line in article.text


def test_inquirer_sept_10_2026_until_dates(index):
    article = next(a for a in ARTICLES if a.date == dt.date(2026, 9, 10))
    found = by_slug(importer.parse_text(index, article.text, article.date))

    assert found["san-simon-pampanga"].lifted_on == dt.date(2026, 9, 12)  # until Friday, Sept. 11
    assert found["santo-tomas-pampanga"].lifted_on == dt.date(2026, 9, 13)  # until Saturday, Sept. 12
    assert found["san-simon-pampanga"].notes  # "except Concepcion Integrated School"
    assert found["masantol"].until_lifted is False


def test_gma_july_21_2025_mixed_scopes(index):
    article = next(a for a in ARTICLES if a.date == dt.date(2025, 7, 21))
    found = by_slug(importer.parse_text(index, article.text, article.date))

    assert found["caloocan-city"].time_scope is TimeScope.AFTERNOON
    assert found["quezon-city"].school_type is SchoolType.PUBLIC
    assert found["quezon-city"].levels == [Level.PRESCHOOL, Level.ELEMENTARY, Level.JUNIOR_HIGH, Level.SENIOR_HIGH]
    assert found["pasig-city"].mode is Mode.FACE_TO_FACE_ONLY
    assert found["masantol"].notes  # "(July 21 and 22)"
    # Pangasinan's San Fernando (in the same article) is not Pampanga's.
    assert "san-fernando-pampanga" not in found
