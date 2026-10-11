import pytest

from app.models import Status
from tests.conftest import ADMIN_HEADERS, TODAY


def payload(**overrides) -> dict:
    return {
        "location": "angeles-city",
        "date": TODAY.isoformat(),
        "levels": ["college"],
        "reason": "weather",
        "reason_detail": "Signal No. 1",
        "source_url": "https://example.org/announcement",
        "source_name": "Angeles City PIO",
        **overrides,
    }


class TestAuth:
    @pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
    def test_create_requires_valid_key(self, client, places, headers):
        response = client.post("/api/v1/suspensions", json=payload(), headers=headers)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
        assert response.headers["WWW-Authenticate"] == "ApiKey"

    def test_verify_requires_valid_key(self, client, places, make_suspension):
        s = make_suspension(places["angeles-city"], status=Status.PENDING)

        assert client.patch(f"/api/v1/suspensions/{s.id}/verify").status_code == 401

    def test_auth_is_checked_before_validation(self, client):
        # Don't reveal validation details to unauthenticated callers.
        assert client.post("/api/v1/suspensions", json={}).status_code == 401


class TestCreate:
    def test_creates_pending_with_defaults(self, client, places):
        response = client.post("/api/v1/suspensions", json=payload(), headers=ADMIN_HEADERS)

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["verified_at"] is None
        assert body["location"]["slug"] == "angeles-city"
        assert body["levels"] == ["college"]
        assert (body["school_type"], body["mode"], body["time_scope"]) == ("both", "all_classes", "whole_day")
        assert body["until_lifted"] is False

    def test_pending_is_not_public(self, client, places):
        client.post("/api/v1/suspensions", json=payload(), headers=ADMIN_HEADERS)

        assert client.get("/api/v1/suspensions").json()["total"] == 0
        assert client.get("/api/v1/check", params={"location": "angeles-city"}).json()["suspended"] is False

    def test_all_level_absorbs_specific_levels(self, client, places):
        response = client.post(
            "/api/v1/suspensions", json=payload(levels=["college", "all", "college"]), headers=ADMIN_HEADERS
        )

        assert response.json()["levels"] == ["all"]

    def test_unknown_location_is_400(self, client, places):
        response = client.post("/api/v1/suspensions", json=payload(location="atlantis"), headers=ADMIN_HEADERS)

        assert response.status_code == 400
        assert response.json()["error"]["details"] == {"field": "location"}

    @pytest.mark.parametrize(
        "overrides",
        [
            {"levels": []},
            {"levels": ["kinder"]},
            {"reason": "aliens"},
            {"source_url": "not-a-url"},
            {"date": "10/12/2026"},
            {"until_lifted": True, "time_scope": "morning"},
        ],
    )
    def test_invalid_payload_is_422(self, client, places, overrides):
        response = client.post("/api/v1/suspensions", json=payload(**overrides), headers=ADMIN_HEADERS)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


class TestVerify:
    def test_verify_makes_it_public(self, client, places):
        created = client.post("/api/v1/suspensions", json=payload(), headers=ADMIN_HEADERS).json()

        response = client.patch(f"/api/v1/suspensions/{created['id']}/verify", headers=ADMIN_HEADERS)

        assert response.status_code == 200
        assert response.json()["status"] == "verified"
        assert response.json()["verified_at"] is not None
        check = client.get("/api/v1/check", params={"location": "angeles-city", "level": "college"}).json()
        assert check["suspended"] is True

    def test_verify_is_idempotent(self, client, places):
        created = client.post("/api/v1/suspensions", json=payload(), headers=ADMIN_HEADERS).json()
        url = f"/api/v1/suspensions/{created['id']}/verify"

        first = client.patch(url, headers=ADMIN_HEADERS).json()
        second = client.patch(url, headers=ADMIN_HEADERS).json()

        assert first["verified_at"] == second["verified_at"]

    def test_missing_is_404(self, client):
        response = client.patch("/api/v1/suspensions/999/verify", headers=ADMIN_HEADERS)

        assert response.status_code == 404
