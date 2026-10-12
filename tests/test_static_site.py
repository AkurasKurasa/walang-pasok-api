"""The GitHub Pages build renders the same templates with static links."""

import re

import pytest

from app.web import routes


@pytest.fixture
def static_mode():
    routes.templates.env.globals.update(static_site=True, site_base="/walang-pasok-api")
    yield
    routes.templates.env.globals.update(static_site=False, site_base="")


def test_place_page_links_work_without_a_server(client, places, make_suspension, static_mode):
    make_suspension(places["angeles-city"], reason_detail="Feast of La Naval")

    html = client.get("/", params={"q": "angeles-city"}).text

    assert "Feast of La Naval" in html
    assert 'href="/walang-pasok-api/static/style.css"' in html  # shared stylesheet, not inlined
    assert 'data-base="/walang-pasok-api/p/"' in html  # dropdown opens /p/<slug>/
    assert 'data-places="/walang-pasok-api/api/v1/locations/grouped.json"' in html
    assert 'href="/walang-pasok-api/api/"' in html  # API docs page instead of /docs
    # Nothing that needs the server: no ?q= links, no sign-up form, no /docs.
    assert not re.search(r'href="/\?', html)
    assert 'action="/alerts"' not in html and 'href="/docs"' not in html


def test_island_filter_works_in_the_page(client, places, make_suspension, static_mode):
    make_suspension(places["quezon-city"])

    html = client.get("/").text

    assert 'data-island="luzon"' in html  # chips and rows carry the island group
    assert '<details class="roster-item" data-island="luzon">' in html


def test_server_mode_is_unchanged(client, places):
    html = client.get("/", params={"q": "angeles-city"}).text

    assert 'href="/docs"' in html and 'action="/alerts"' in html
    assert "<style>" in html  # CSS still inlined for one-request loads
