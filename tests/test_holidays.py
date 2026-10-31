"""National holidays from Proclamation No. 1006 (s. 2025) become nationwide suspensions."""

import datetime as dt
import json

import pytest

from app.models import Location, LocationType, Reason, Status, Suspension
from app.services import holidays
from scripts.seed import upsert_locations

ALL_SOULS = dt.date(2026, 11, 2)
EDSA = dt.date(2026, 2, 25)


@pytest.fixture
def seeded(db):
    upsert_locations(db)
    return db


@pytest.fixture
def synced(seeded):
    holidays.sync(seeded, holidays.load_all())
    return seeded


def write_file(tmp_path, name="2026.json", **overrides):
    data = {
        "year": 2026,
        "source": {"name": "Proclamation No. 1006, s. 2025", "url": "https://lawphil.example/proc_1006.html"},
        "holidays": [{"date": "2026-11-02", "name": "All Souls' Day", "type": "special_non_working"}],
        **overrides,
    }
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestTheProclamationFile:
    def test_matches_proclamation_1006(self):
        listed = {h.date: h for h in holidays.load(holidays.DATA_DIR / "2026.json")}

        assert len(listed) == 19  # 10 regular, 4 special, 1 special working, 4 additional special
        assert sum(h.type == "regular" for h in listed.values()) == 10
        assert listed[ALL_SOULS].name == "All Souls' Day" and listed[ALL_SOULS].no_classes
        assert listed[dt.date(2026, 11, 1)].name == "All Saints' Day"
        assert listed[dt.date(2026, 11, 30)].type == "regular"  # Bonifacio Day
        assert not listed[EDSA].no_classes  # special *working* day: classes go on

    def test_excerpt_reads_like_the_proclamation(self):
        h = next(h for h in holidays.load_all() if h.date == ALL_SOULS)
        assert h.excerpt == "All Souls' Day - 2 November (Monday)"
        assert h.detail == "All Souls' Day (special non-working day)"


class TestSync:
    def test_no_classes_on_all_souls_day_anywhere(self, client, synced):
        for slug in ("angeles-city", "quezon-city", "mamburao", "cebu-city"):
            body = client.get("/api/v1/check", params={"location": slug, "date": ALL_SOULS.isoformat()}).json()
            assert body["suspended"] is True, slug
            [detail] = body["details"]
            assert detail["location"]["slug"] == "philippines"
            assert detail["reason"] == "holiday"
            assert detail["reason_detail"] == "All Souls' Day (special non-working day)"
            assert detail["proof"][0]["excerpt"] == "All Souls' Day - 2 November (Monday)"

    def test_working_holiday_is_not_a_suspension(self, client, synced):
        body = client.get("/api/v1/check", params={"location": "angeles-city", "date": EDSA.isoformat()}).json()

        assert body["suspended"] is False

    def test_ordinary_day_is_unaffected(self, client, synced):
        body = client.get("/api/v1/check", params={"location": "angeles-city", "date": "2026-11-03"}).json()

        assert body["suspended"] is False

    def test_holidays_are_verified_and_public(self, synced):
        rows = synced.query(Suspension).filter(Suspension.reason == Reason.HOLIDAY).all()

        assert len(rows) == 18  # 19 listed, minus the EDSA working day
        assert {s.status for s in rows} == {Status.VERIFIED}

    def test_running_again_adds_nothing(self, synced):
        report = holidays.sync(synced, holidays.load_all())

        assert report.created == [] and report.already_listed == 18 and report.working_days_skipped == 1
        assert synced.query(Suspension).count() == 18

    def test_needs_the_country_location(self, db):
        with pytest.raises(RuntimeError, match="philippines"):
            holidays.sync(db, holidays.load_all())


class TestBadFiles:
    @pytest.mark.parametrize(("overrides", "message"), [
        ({"holidays": [{"date": "2026-11-02", "name": "All Souls' Day", "type": "holiday"}]}, "type must be"),
        ({"holidays": [{"date": "2025-11-02", "name": "All Souls' Day", "type": "regular"}]}, "isn't in 2026"),
        ({"holidays": [{"date": "11/02/2026", "name": "All Souls' Day", "type": "regular"}]}, "YYYY-MM-DD"),
        ({"holidays": [{"date": "2026-11-02", "name": " ", "type": "regular"}]}, "name is empty"),
        ({"holidays": [{"date": "2026-11-02", "name": "A", "type": "regular"},
                       {"date": "2026-11-02", "name": "B", "type": "regular"}]}, "listed twice"),
        ({"source": {"name": "Proclamation", "url": "lawphil.net/proc"}}, "http"),
        ({"source": {}}, "needs year"),
    ])
    def test_fails_loudly(self, tmp_path, overrides, message):
        with pytest.raises(holidays.HolidayDataError, match=message):
            holidays.load(write_file(tmp_path, **overrides))

    def test_file_name_must_match_the_year(self, tmp_path):
        with pytest.raises(holidays.HolidayDataError, match="file name must match"):
            holidays.load(write_file(tmp_path, name="2027.json"))

    def test_unreadable_json(self, tmp_path):
        path = tmp_path / "2026.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(holidays.HolidayDataError, match="can't read"):
            holidays.load(path)


def test_warns_when_next_years_file_is_missing(tmp_path):
    write_file(tmp_path)

    assert holidays.missing_next_year(dt.date(2026, 10, 31), tmp_path) == 2027
    assert holidays.missing_next_year(dt.date(2026, 9, 30), tmp_path) is None  # too early to expect it
    write_file(tmp_path, name="2027.json", year=2027,
               holidays=[{"date": "2027-01-01", "name": "New Year's Day", "type": "regular"}])
    assert holidays.missing_next_year(dt.date(2026, 10, 31), tmp_path) is None


def test_country_sits_above_every_region(seeded):
    country = seeded.query(Location).filter_by(slug="philippines").one()
    regions = seeded.query(Location).filter_by(type=LocationType.REGION).all()

    assert country.type is LocationType.COUNTRY and country.parent is None
    assert len(regions) == 17 and {r.parent_id for r in regions} == {country.id}


def test_news_never_creates_a_nationwide_suspension(seeded):
    from app.services import importer

    index = importer.PlaceIndex.from_db(seeded)
    for line in ("Philippines - All levels, public and private", "MANILA, Philippines — Classes suspended at all levels"):
        result = importer.parse_text(index, line, ALL_SOULS, Reason.WEATHER)
        assert result.candidates == [], line
