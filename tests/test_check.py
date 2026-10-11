import datetime as dt

import pytest

from app.models import Level, SchoolType, Status
from tests.conftest import TODAY, TOMORROW, YESTERDAY


def check(client, **params):
    response = client.get("/api/v1/check", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_suspended_when_verified_suspension_matches(client, places, make_suspension):
    make_suspension(places["angeles-city"], date=TODAY, reason_detail="Signal No. 2")

    body = check(client, location="angeles-city", date=TODAY.isoformat())

    assert body["suspended"] is True
    assert body["location"]["slug"] == "angeles-city"
    assert body["date"] == TODAY.isoformat()
    assert [d["reason_detail"] for d in body["details"]] == ["Signal No. 2"]
    assert body["sources"] == [
        {"name": "Angeles City PIO", "url": "https://example.org/angeles-city"}
    ]


def test_not_suspended_when_nothing_reported(client, places):
    body = check(client, location="angeles-city")

    assert body == {
        "suspended": False,
        "location": body["location"],
        "date": TODAY.isoformat(),
        "level": None,
        "school_type": None,
        "details": [],
        "sources": [],
    }


def test_date_defaults_to_today_in_manila(client, places, make_suspension):
    make_suspension(places["angeles-city"], date=TODAY)

    body = check(client, location="angeles-city")

    assert body["date"] == TODAY.isoformat()
    assert body["suspended"] is True


def test_other_dates_are_not_suspended(client, places, make_suspension):
    make_suspension(places["angeles-city"], date=TODAY)

    assert check(client, location="angeles-city", date=TOMORROW.isoformat())["suspended"] is False
    assert check(client, location="angeles-city", date=YESTERDAY.isoformat())["suspended"] is False


def test_pending_suspensions_are_ignored(client, places, make_suspension):
    make_suspension(places["angeles-city"], status=Status.PENDING)

    assert check(client, location="angeles-city")["suspended"] is False


@pytest.mark.parametrize(
    ("declared", "queried", "expected"),
    [
        ([Level.ALL], "college", True),  # "all" covers every level
        ([Level.COLLEGE], "college", True),
        ([Level.ELEMENTARY, Level.JUNIOR_HIGH], "junior_high", True),
        ([Level.ELEMENTARY, Level.JUNIOR_HIGH], "college", False),
        ([Level.COLLEGE], "all", False),  # asking about "all" needs an all-levels suspension
        ([Level.ALL], "all", True),
    ],
)
def test_level_matching(client, places, make_suspension, declared, queried, expected):
    make_suspension(places["angeles-city"], levels=declared)

    assert check(client, location="angeles-city", level=queried)["suspended"] is expected


def test_no_level_means_any_level(client, places, make_suspension):
    make_suspension(places["angeles-city"], levels=[Level.PRESCHOOL])

    assert check(client, location="angeles-city")["suspended"] is True


@pytest.mark.parametrize(
    ("declared", "queried", "expected"),
    [
        (SchoolType.PUBLIC, "public", True),
        (SchoolType.PUBLIC, "private", False),
        (SchoolType.BOTH, "private", True),
        (SchoolType.PRIVATE, None, True),
    ],
)
def test_school_type_matching(client, places, make_suspension, declared, queried, expected):
    make_suspension(places["angeles-city"], school_type=declared)
    params = {"location": "angeles-city"}
    if queried:
        params["school_type"] = queried

    assert check(client, **params)["suspended"] is expected


class TestUntilLifted:
    def test_covers_start_and_later_days(self, client, places, make_suspension):
        make_suspension(places["quezon-city"], date=YESTERDAY, until_lifted=True)

        for day in (YESTERDAY, TODAY, TODAY + dt.timedelta(days=30)):
            assert check(client, location="quezon-city", date=day.isoformat())["suspended"] is True

    def test_does_not_cover_days_before_start(self, client, places, make_suspension):
        make_suspension(places["quezon-city"], date=TODAY, until_lifted=True)

        assert check(client, location="quezon-city", date=YESTERDAY.isoformat())["suspended"] is False

    def test_ends_when_lifted(self, client, places, make_suspension):
        make_suspension(
            places["quezon-city"], date=YESTERDAY, until_lifted=True, lifted_on=TOMORROW
        )

        assert check(client, location="quezon-city", date=TODAY.isoformat())["suspended"] is True
        assert check(client, location="quezon-city", date=TOMORROW.isoformat())["suspended"] is False

    def test_regular_suspension_does_not_carry_over(self, client, places, make_suspension):
        make_suspension(places["quezon-city"], date=YESTERDAY, until_lifted=False)

        assert check(client, location="quezon-city")["suspended"] is False


class TestLocationHierarchy:
    def test_province_wide_suspension_covers_component_city(self, client, places, make_suspension):
        make_suspension(places["pampanga"])

        body = check(client, location="san-fernando")

        assert body["suspended"] is True
        assert body["details"][0]["location"]["slug"] == "pampanga"

    def test_province_wide_suspension_skips_independent_city(self, client, places, make_suspension):
        make_suspension(places["pampanga"])

        assert check(client, location="angeles-city")["suspended"] is False

    def test_region_wide_suspension_covers_every_city(self, client, places, make_suspension):
        make_suspension(places["central-luzon"])

        assert check(client, location="angeles-city")["suspended"] is True
        assert check(client, location="san-fernando")["suspended"] is True
        assert check(client, location="quezon-city")["suspended"] is False

    def test_city_suspension_does_not_cover_its_province(self, client, places, make_suspension):
        make_suspension(places["san-fernando"])

        assert check(client, location="pampanga")["suspended"] is False


def test_sources_are_deduplicated(client, places, make_suspension):
    shared = {"source_url": "https://example.org/same", "source_name": "Same PIO"}
    make_suspension(places["san-fernando"], levels=[Level.ELEMENTARY], **shared)
    make_suspension(places["san-fernando"], levels=[Level.COLLEGE], **shared)

    body = check(client, location="san-fernando")

    assert len(body["details"]) == 2
    assert body["sources"] == [{"name": "Same PIO", "url": "https://example.org/same"}]


def test_slug_lookup_is_case_insensitive(client, places, make_suspension):
    make_suspension(places["angeles-city"])

    assert check(client, location="Angeles-City")["suspended"] is True


class TestErrors:
    def test_unknown_location_is_404(self, client, places):
        response = client.get("/api/v1/check", params={"location": "atlantis"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        assert "atlantis" in response.json()["error"]["message"]

    def test_missing_location_is_422(self, client):
        response = client.get("/api/v1/check")

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert error["details"][0]["loc"] == ["query", "location"]

    @pytest.mark.parametrize("params", [{"level": "kinder"}, {"date": "2026-13-01"}, {"date": "tomorrow"}])
    def test_invalid_params_are_422(self, client, places, params):
        response = client.get("/api/v1/check", params={"location": "angeles-city", **params})

        assert response.status_code == 422
