import datetime as dt

from app.models import Level, Status
from tests.conftest import TODAY, TOMORROW, YESTERDAY


def list_ids(client, path="/api/v1/suspensions", **params) -> list[int]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_lists_only_verified(client, places, make_suspension):
    verified = make_suspension(places["angeles-city"])
    make_suspension(places["quezon-city"], status=Status.PENDING)

    assert list_ids(client) == [verified.id]


def test_newest_first(client, places, make_suspension):
    old = make_suspension(places["angeles-city"], date=YESTERDAY)
    new = make_suspension(places["angeles-city"], date=TOMORROW)

    assert list_ids(client) == [new.id, old.id]


def test_response_shape(client, places, make_suspension):
    make_suspension(places["angeles-city"], levels=[Level.ELEMENTARY, Level.COLLEGE])

    body = client.get("/api/v1/suspensions").json()

    assert body["total"] == 1 and body["page"] == 1 and body["pages"] == 1
    item = body["items"][0]
    assert item["location"]["psgc_code"] == "0330100000"
    assert sorted(item["levels"]) == ["college", "elementary"]
    assert item["status"] == "verified"
    assert {"school_type", "mode", "time_scope", "until_lifted", "reason", "source_url"} <= item.keys()


class TestFilters:
    def test_by_date(self, client, places, make_suspension):
        today = make_suspension(places["angeles-city"], date=TODAY)
        make_suspension(places["angeles-city"], date=TOMORROW)

        assert list_ids(client, date=TODAY.isoformat()) == [today.id]

    def test_by_date_includes_ongoing_until_lifted(self, client, places, make_suspension):
        ongoing = make_suspension(places["quezon-city"], date=YESTERDAY, until_lifted=True)

        assert list_ids(client, date=TODAY.isoformat()) == [ongoing.id]

    def test_by_location_includes_parent_areas(self, client, places, make_suspension):
        city = make_suspension(places["san-fernando"])
        province = make_suspension(places["pampanga"], date=TOMORROW)
        make_suspension(places["angeles-city"])

        assert list_ids(client, location="san-fernando") == [province.id, city.id]

    def test_by_level(self, client, places, make_suspension):
        college = make_suspension(places["angeles-city"], levels=[Level.COLLEGE])
        everyone = make_suspension(places["quezon-city"], levels=[Level.ALL])
        make_suspension(places["san-fernando"], levels=[Level.ELEMENTARY])

        assert sorted(list_ids(client, level="college")) == sorted([college.id, everyone.id])

    def test_combined(self, client, places, make_suspension):
        match = make_suspension(places["angeles-city"], levels=[Level.COLLEGE])
        make_suspension(places["angeles-city"], levels=[Level.COLLEGE], date=TOMORROW)
        make_suspension(places["angeles-city"], levels=[Level.ELEMENTARY])
        make_suspension(places["quezon-city"], levels=[Level.COLLEGE])

        ids = list_ids(client, date=TODAY.isoformat(), location="angeles-city", level="college")

        assert ids == [match.id]

    def test_unknown_location_is_400(self, client, places):
        response = client.get("/api/v1/suspensions", params={"location": "atlantis"})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "bad_request"

    def test_invalid_level_is_422(self, client):
        assert client.get("/api/v1/suspensions", params={"level": "phd"}).status_code == 422


class TestPagination:
    def test_pages(self, client, places, make_suspension):
        for i in range(5):
            make_suspension(places["angeles-city"], date=TODAY - dt.timedelta(days=i))

        first = client.get("/api/v1/suspensions", params={"size": 2}).json()
        last = client.get("/api/v1/suspensions", params={"size": 2, "page": 3}).json()

        assert (first["total"], first["pages"], len(first["items"])) == (5, 3, 2)
        assert len(last["items"]) == 1
        assert first["items"][0]["date"] == TODAY.isoformat()

    def test_page_past_the_end_is_empty(self, client, places, make_suspension):
        make_suspension(places["angeles-city"])

        body = client.get("/api/v1/suspensions", params={"page": 9}).json()

        assert body["items"] == [] and body["total"] == 1

    def test_empty_result(self, client):
        body = client.get("/api/v1/suspensions").json()

        assert body == {"items": [], "total": 0, "page": 1, "size": 20, "pages": 0}

    def test_bounds_are_validated(self, client):
        assert client.get("/api/v1/suspensions", params={"size": 101}).status_code == 422
        assert client.get("/api/v1/suspensions", params={"size": 0}).status_code == 422
        assert client.get("/api/v1/suspensions", params={"page": 0}).status_code == 422


class TestToday:
    def test_returns_todays_verified_nationwide(self, client, places, make_suspension):
        angeles = make_suspension(places["angeles-city"], date=TODAY)
        qc = make_suspension(places["quezon-city"], date=YESTERDAY, until_lifted=True)
        make_suspension(places["san-fernando"], date=TOMORROW)
        make_suspension(places["pampanga"], date=TODAY, status=Status.PENDING)

        assert sorted(list_ids(client, "/api/v1/suspensions/today")) == sorted([angeles.id, qc.id])

    def test_filters_by_level(self, client, places, make_suspension):
        make_suspension(places["angeles-city"], levels=[Level.ELEMENTARY])
        college = make_suspension(places["quezon-city"], levels=[Level.COLLEGE])

        assert list_ids(client, "/api/v1/suspensions/today", level="college") == [college.id]


class TestGetOne:
    def test_verified(self, client, places, make_suspension):
        s = make_suspension(places["angeles-city"])

        response = client.get(f"/api/v1/suspensions/{s.id}")

        assert response.status_code == 200
        assert response.json()["id"] == s.id

    def test_pending_is_hidden(self, client, places, make_suspension):
        s = make_suspension(places["angeles-city"], status=Status.PENDING)

        assert client.get(f"/api/v1/suspensions/{s.id}").status_code == 404

    def test_missing(self, client):
        response = client.get("/api/v1/suspensions/999")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
