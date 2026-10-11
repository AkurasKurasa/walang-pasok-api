from app.models import SourceKind
from app.models.source import infer_kind
from tests.conftest import ADMIN_HEADERS, TODAY


def payload(**overrides) -> dict:
    return {
        "location": "angeles-city",
        "date": TODAY.isoformat(),
        "levels": ["all"],
        "reason": "weather",
        "source_url": "https://www.facebook.com/AngelesCityGov/posts/123",
        "source_name": "Angeles City Information Office",
        **overrides,
    }


def test_infer_kind_from_url():
    assert infer_kind("https://www.facebook.com/x/posts/1") is SourceKind.FACEBOOK_POST
    assert infer_kind("https://m.facebook.com/story.php?id=1") is SourceKind.FACEBOOK_POST
    assert infer_kind("https://x.com/pagasa/status/1") is SourceKind.X_POST
    assert infer_kind("https://www.deped.gov.ph/advisory") is SourceKind.OFFICIAL_WEBSITE
    assert infer_kind("https://news.example.com/a") is SourceKind.OTHER
    # Look-alike hosts are not Facebook.
    assert infer_kind("https://notfacebook.com/a") is SourceKind.OTHER


def test_primary_source_is_first_proof(client, places):
    body = client.post(
        "/api/v1/suspensions",
        json=payload(source_excerpt="Walang pasok bukas, lahat ng antas.", source_posted_at="2026-10-11T20:15:00+08:00"),
        headers=ADMIN_HEADERS,
    ).json()

    assert body["proof"] == [
        {
            "kind": "facebook_post",
            "name": "Angeles City Information Office",
            "url": "https://www.facebook.com/AngelesCityGov/posts/123",
            "excerpt": "Walang pasok bukas, lahat ng antas.",
            "posted_at": body["proof"][0]["posted_at"],
            "primary": True,
        }
    ]
    assert body["proof"][0]["posted_at"].startswith("2026-10-11")


def test_extra_sources_follow_the_primary(client, places):
    body = client.post(
        "/api/v1/suspensions",
        json=payload(extra_sources=[
            {"url": "https://news.example.com/walang-pasok", "name": "Example News", "kind": "news_article"},
        ]),
        headers=ADMIN_HEADERS,
    ).json()

    assert [p["primary"] for p in body["proof"]] == [True, False]
    assert body["proof"][1]["kind"] == "news_article"


def test_add_source_endpoint(client, places, make_suspension):
    s = make_suspension(places["angeles-city"])

    response = client.post(
        f"/api/v1/suspensions/{s.id}/sources",
        json={"url": "https://news.example.com/a", "name": "Example News", "excerpt": "Classes suspended."},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 201
    proof = response.json()["proof"]
    assert len(proof) == 2
    assert proof[1] == {
        "kind": "other", "name": "Example News", "url": "https://news.example.com/a",
        "excerpt": "Classes suspended.", "posted_at": None, "primary": False,
    }


def test_add_source_needs_admin(client, places, make_suspension):
    s = make_suspension(places["angeles-city"])

    response = client.post(f"/api/v1/suspensions/{s.id}/sources", json={"url": "https://a.example", "name": "A"})

    assert response.status_code == 401


def test_add_source_to_missing_suspension(client):
    response = client.post(
        "/api/v1/suspensions/999/sources", json={"url": "https://a.example", "name": "A"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 404


def test_check_returns_proof_in_details(client, places, make_suspension):
    make_suspension(places["angeles-city"], source_url="https://www.facebook.com/acio/posts/9")

    detail = client.get("/api/v1/check", params={"location": "angeles-city"}).json()["details"][0]

    assert detail["proof"][0]["kind"] == "facebook_post"
