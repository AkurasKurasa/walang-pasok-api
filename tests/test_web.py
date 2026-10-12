from app.models import Status
from app.services import clock
from tests.conftest import TOMORROW


def test_homepage_renders(client, places):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "May pasok ba bukas?" in response.text
    assert 'href="/docs"' in response.text
    assert "No verified suspensions today." in response.text


def test_nationwide_list_shows_verified_today(client, places, make_suspension):
    make_suspension(places["quezon-city"], reason_detail="Flooding")
    make_suspension(places["angeles-city"], reason_detail="Hidden pending", status=Status.PENDING)

    html = client.get("/").text

    assert "Flooding" in html
    assert "Hidden pending" not in html


def test_search_shows_tomorrow_and_today(client, places, make_suspension):
    make_suspension(places["angeles-city"], date=TOMORROW, reason_detail="Signal No. 2")

    html = client.get("/", params={"q": "Angeles City"}).text

    assert "Bukas" in html and "Ngayon" in html
    assert "Walang pasok bukas" in html
    assert "Signal No. 2" in html
    assert "No suspension announced" in html  # today


def test_no_suspension_says_may_pasok(client, places):
    html = client.get("/", params={"q": "angeles-city"}).text

    assert "May pasok bukas" in html
    assert "panel-on" in html


def test_search_by_slug(client, places):
    html = client.get("/", params={"q": "quezon-city"}).text

    assert '<p class="panel-place">Quezon City' in html


def test_ambiguous_search_offers_choices(client, places):
    html = client.get("/", params={"q": "city"}).text

    assert "Which one did you mean?" in html
    assert "/?q=angeles-city" in html


def test_unknown_search(client, places):
    html = client.get("/", params={"q": "atlantis"}).text

    assert "couldn&#39;t find" in html or "couldn't find" in html


def test_html_is_escaped(client, places):
    html = client.get("/", params={"q": "<script>x</script>"}).text

    assert "<script>x</script>" not in html


def test_clock_uses_manila_time():
    assert clock.now().utcoffset().total_seconds() == 8 * 3600


WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 68


def test_slip_shows_proof_toggle_and_quote(client, places, make_suspension):
    make_suspension(
        places["angeles-city"], date=TOMORROW, reason_detail="Signal No. 2",
        source_url="https://www.facebook.com/acio/posts/1", source_excerpt="Walang pasok bukas!",
    )

    html = client.get("/", params={"q": "angeles-city"}).text

    assert 'class="proof"' in html and "Show proof" in html
    assert "Walang pasok bukas!" in html
    assert "Facebook post" in html
    assert 'href="https://www.facebook.com/acio/posts/1"' in html
    assert "Verified, 1 source" in html


def test_timetable_only_after_search(client, places, make_suspension):
    make_suspension(places["angeles-city"])

    assert 'class="timetable"' not in client.get("/").text
    html = client.get("/", params={"q": "angeles-city"}).text
    assert 'class="timetable"' in html
    assert "No class" in html


def test_roll_call_rows_have_proof(client, places, make_suspension):
    make_suspension(places["quezon-city"], source_excerpt="Suspended due to flooding.")

    html = client.get("/").text

    assert 'class="roster-item"' in html
    assert "Suspended due to flooding." in html


def test_island_filter(client, places, make_suspension):
    make_suspension(places["quezon-city"], reason_detail="Flooding in QC")

    assert "Flooding in QC" in client.get("/", params={"island": "luzon"}).text
    html = client.get("/", params={"island": "visayas"}).text
    assert "Flooding in QC" not in html
    assert "No verified suspensions in Visayas today." in html
    # Unknown values are ignored rather than erroring.
    assert client.get("/", params={"island": "atlantis"}).status_code == 200


def test_alert_signup_form(client, places, outbox):
    response = client.post("/alerts", data={"webhook_url": WEBHOOK, "location": "Angeles City"})

    assert response.status_code == 201
    assert "Ring ring!" in response.text and "Angeles City" in response.text
    assert len(outbox.requests) == 1  # welcome message


def test_alert_signup_form_errors(client, places):
    bad_url = client.post("/alerts", data={"webhook_url": "https://example.com/hook"})
    bad_place = client.post("/alerts", data={"webhook_url": WEBHOOK, "location": "Atlantis"})

    assert bad_url.status_code == 400 and "Not a Discord webhook URL" in bad_url.text
    assert bad_place.status_code == 400 and "couldn" in bad_place.text


def test_alert_turn_off_form(client, places):
    html = client.post("/alerts", data={"webhook_url": WEBHOOK}).text
    token = html.split("<code>")[1].split("</code>")[0]

    assert client.post("/alerts/off", data={"token": token}).status_code == 200
    assert client.post("/alerts/off", data={"token": token}).status_code == 404


def test_favicon(client):
    ico = client.get("/favicon.ico")
    svg = client.get("/static/favicon.svg")

    assert ico.status_code == 200 and ico.headers["content-type"] == "image/x-icon"
    assert ico.content[:4] == b"\x00\x00\x01\x00"  # ICO file signature
    assert svg.status_code == 200 and "svg" in svg.headers["content-type"]
    assert client.get("/static/apple-touch-icon.png").status_code == 200


def test_pages_link_the_icons(client, places):
    html = client.get("/").text

    assert '<link rel="icon" href="/favicon.ico"' in html
    assert 'href="/static/favicon.svg"' in html


def test_date_param_shows_that_day(client, places, make_suspension):
    import datetime as dt

    day = dt.date(2025, 7, 21)
    make_suspension(places["quezon-city"], date=day, reason_detail="Habagat")

    html = client.get("/", params={"date": "2025-07-21", "q": "quezon-city"}).text

    assert "Looking back" in html and "Monday, July 21, 2025" in html
    assert "Habagat" in html
    assert 'name="date" value="2025-07-21"' in html  # search keeps the date
    assert "/?date=2025-07-21&amp;q=" in html or "/?date=2025-07-21&q=" in html


def test_recent_days_offered_when_today_is_quiet(client, places, make_suspension):
    import datetime as dt

    make_suspension(places["quezon-city"], date=dt.date(2025, 7, 21))

    html = client.get("/").text

    assert "Recent days with suspensions" in html
    assert 'href="/?date=2025-07-21#roster"' in html


def test_bad_date_is_422(client):
    assert client.get("/", params={"date": "yesterday"}).status_code == 422


def test_posted_time_shown_in_manila_time(client, places):
    from tests.conftest import ADMIN_HEADERS

    body = {
        "location": "quezon-city", "date": "2026-10-12", "levels": ["all"], "reason": "weather",
        "source_url": "https://quezoncity.gov.ph/a", "source_name": "Quezon City Government",
        "source_posted_at": "2026-10-11T13:23:00+08:00",
    }
    s = client.post("/api/v1/suspensions", json=body, headers=ADMIN_HEADERS).json()
    client.patch(f"/api/v1/suspensions/{s['id']}/verify", headers=ADMIN_HEADERS)

    html = client.get("/", params={"q": "quezon-city"}).text

    assert "Posted Oct 11, 1:23 PM" in html
    assert "Official source" in html


def test_city_dropdown_lists_places_by_province(client, places):
    html = client.get("/").text

    assert '<optgroup label="Pampanga">' in html
    assert '<option value="angeles-city"' in html
    assert '<optgroup label="Metro Manila">' in html  # NCR cities have no province


def test_alert_links_follow_the_hosting_style():
    from app.config import Settings
    from app.models import Location, Suspension
    from app.services import notifications

    s = Suspension(location=Location(slug="angeles-city"))
    assert notifications.site_link(s, Settings(public_base_url="https://x.example")) == "https://x.example/?q=angeles-city"
    pages = Settings(public_base_url="https://u.github.io/repo/", static_site_links=True)
    assert notifications.site_link(s, pages) == "https://u.github.io/repo/p/angeles-city/"
