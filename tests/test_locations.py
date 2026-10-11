def slugs(client, **params) -> list[str]:
    response = client.get("/api/v1/locations", params=params)
    assert response.status_code == 200, response.text
    return [loc["slug"] for loc in response.json()["items"]]


def test_search_by_name(client, places):
    assert slugs(client, search="angeles") == ["angeles-city"]


def test_search_is_case_insensitive(client, places):
    assert slugs(client, search="QUEZON") == ["quezon-city"]


def test_search_matches_province(client, places):
    assert slugs(client, search="pampanga") == ["angeles-city", "san-fernando", "pampanga"]


def test_no_search_lists_everything_paginated(client, places):
    body = client.get("/api/v1/locations", params={"size": 2}).json()

    assert body["total"] == len(places)
    assert len(body["items"]) == 2


def test_no_match(client, places):
    assert slugs(client, search="atlantis") == []


def test_wildcards_are_literal(client, places):
    assert slugs(client, search="%") == []
    assert slugs(client, search="_") == []


def test_response_includes_psgc_fields(client, places):
    item = client.get("/api/v1/locations", params={"search": "angeles"}).json()["items"][0]

    assert item == {
        "psgc_code": "0330100000",
        "name": "Angeles City",
        "slug": "angeles-city",
        "type": "city",
        "region": "Central Luzon",
        "province": "Pampanga",
    }
