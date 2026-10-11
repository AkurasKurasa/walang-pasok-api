import datetime as dt

import pytest

from app.config import Settings
from app.models import Level, Location, Status, Subscription, Suspension
from app.services import notifications
from tests.conftest import ADMIN_HEADERS, TODAY, YESTERDAY

WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 68
WEBHOOK_2 = "https://discord.com/api/webhooks/223456789012345678/" + "b" * 68


def subscribe(client, location=None, webhook=WEBHOOK, **extra):
    body = {"webhook_url": webhook, "send_test": False, **extra}
    if location:
        body["location"] = location
    return client.post("/api/v1/alerts/discord", json=body)


def verify(client, suspension_id: int):
    return client.patch(f"/api/v1/suspensions/{suspension_id}/verify", headers=ADMIN_HEADERS)


class TestSignup:
    def test_creates_alert_and_hides_webhook(self, client, places):
        response = subscribe(client, "angeles-city", label="AUF CCS")

        assert response.status_code == 201
        body = response.json()
        assert body["location"]["slug"] == "angeles-city"
        assert body["label"] == "AUF CCS"
        assert len(body["manage_token"]) >= 20
        assert "webhook" not in str(body).lower()

    def test_nationwide_when_no_location(self, client, places):
        assert subscribe(client).json()["location"] is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/api/webhooks/1/abc",
            "http://discord.com/api/webhooks/123456789012345678/" + "a" * 68,
            "https://discord.com.evil.example/api/webhooks/123456789012345678/" + "a" * 68,
            "https://discord.com/api/webhooks/123456789012345678",
            "not a url",
        ],
    )
    def test_rejects_non_discord_urls(self, client, places, url):
        response = subscribe(client, webhook=url)

        assert response.status_code == 422

    def test_accepts_discordapp_and_whitespace(self, client, places):
        url = "  https://discordapp.com/api/webhooks/123456789012345678/" + "c" * 68 + " "

        assert subscribe(client, webhook=url).status_code == 201

    def test_unknown_location(self, client, places):
        response = subscribe(client, "atlantis")

        assert response.status_code == 400
        assert response.json()["error"]["details"] == {"field": "location"}

    def test_duplicate_is_409(self, client, places):
        subscribe(client, "angeles-city")

        response = subscribe(client, "angeles-city")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_same_webhook_can_follow_two_places(self, client, places):
        assert subscribe(client, "angeles-city").status_code == 201
        assert subscribe(client, "quezon-city").status_code == 201

    def test_welcome_message_is_sent(self, client, places, outbox):
        client.post("/api/v1/alerts/discord", json={"webhook_url": WEBHOOK, "location": "angeles-city"})

        assert len(outbox.requests) == 1
        assert outbox.requests[0]["url"] == WEBHOOK
        assert "Angeles City" in outbox.json_bodies()[0]["content"]

    def test_unsubscribe(self, client, places, db):
        token = subscribe(client, "angeles-city").json()["manage_token"]

        assert client.delete(f"/api/v1/alerts/{token}").status_code == 204
        assert db.query(Subscription).one().active is False
        assert client.delete(f"/api/v1/alerts/{token}").status_code == 404

    def test_resubscribe_after_unsubscribe(self, client, places):
        first = subscribe(client, "angeles-city").json()["manage_token"]
        client.delete(f"/api/v1/alerts/{first}")

        again = subscribe(client, "angeles-city")

        assert again.status_code == 201
        assert again.json()["manage_token"] != first


class TestDispatchOnVerify:
    def test_verify_sends_to_matching_channel(self, client, places, make_suspension, outbox):
        subscribe(client, "angeles-city")
        s = make_suspension(places["angeles-city"], status=Status.PENDING, reason_detail="Signal No. 2")

        verify(client, s.id)

        assert [r["url"] for r in outbox.requests] == [WEBHOOK]
        embed = outbox.json_bodies()[0]["embeds"][0]
        assert "Angeles City" in embed["title"]
        assert embed["url"] == "http://localhost:8000/?q=angeles-city"
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert fields["Why"] == "Signal No. 2"
        assert "example.org/angeles-city" in fields["Proof"]

    def test_never_pings_everyone(self, client, places, make_suspension, outbox):
        subscribe(client)
        s = make_suspension(places["angeles-city"], status=Status.PENDING, reason_detail="@everyone")

        verify(client, s.id)

        assert outbox.json_bodies()[0]["allowed_mentions"] == {"parse": []}

    def test_only_first_verify_sends(self, client, places, make_suspension, outbox):
        subscribe(client, "angeles-city")
        s = make_suspension(places["angeles-city"], status=Status.PENDING)

        verify(client, s.id)
        verify(client, s.id)

        assert len(outbox.requests) == 1

    def test_other_places_are_not_told(self, client, places, make_suspension, outbox):
        subscribe(client, "quezon-city")
        s = make_suspension(places["angeles-city"], status=Status.PENDING)

        verify(client, s.id)

        assert outbox.requests == []

    def test_region_wide_reaches_city_followers(self, client, places, make_suspension, outbox):
        subscribe(client, "quezon-city")
        s = make_suspension(places["metro-manila"], status=Status.PENDING)

        verify(client, s.id)

        assert len(outbox.requests) == 1

    def test_province_followers_hear_about_cities_inside(self, client, places, make_suspension, outbox):
        subscribe(client, "pampanga")
        s = make_suspension(places["san-fernando"], status=Status.PENDING)

        verify(client, s.id)

        assert len(outbox.requests) == 1

    def test_province_wide_does_not_reach_independent_city(self, client, places, make_suspension, outbox):
        # Angeles is highly urbanized: a Pampanga-wide suspension doesn't cover it.
        subscribe(client, "angeles-city")
        s = make_suspension(places["pampanga"], status=Status.PENDING)

        verify(client, s.id)

        assert outbox.requests == []

    def test_nationwide_followers_hear_everything(self, client, places, make_suspension, outbox):
        subscribe(client)
        s = make_suspension(places["quezon-city"], status=Status.PENDING)

        verify(client, s.id)

        assert len(outbox.requests) == 1

    def test_past_suspensions_are_not_announced(self, client, places, make_suspension, outbox):
        subscribe(client)
        s = make_suspension(places["quezon-city"], status=Status.PENDING, date=YESTERDAY)

        verify(client, s.id)

        assert outbox.requests == []

    def test_ongoing_until_lifted_is_announced(self, client, places, make_suspension, outbox):
        subscribe(client)
        s = make_suspension(places["quezon-city"], status=Status.PENDING, date=YESTERDAY, until_lifted=True)

        verify(client, s.id)

        assert len(outbox.requests) == 1

    def test_dead_webhook_is_switched_off(self, client, places, make_suspension, outbox, db):
        subscribe(client, "angeles-city")
        outbox.next_status = 404
        s = make_suspension(places["angeles-city"], status=Status.PENDING)

        verify(client, s.id)

        sub = db.query(Subscription).one()
        assert sub.active is False
        assert sub.failure_count == 1
        assert "404" in sub.last_error

    def test_temporary_failure_keeps_it_on(self, client, places, make_suspension, outbox, db):
        subscribe(client, "angeles-city")
        outbox.next_status = 500
        s = make_suspension(places["angeles-city"], status=Status.PENDING)

        verify(client, s.id)

        sub = db.query(Subscription).one()
        assert sub.active is True
        assert sub.failure_count == 1

    def test_success_resets_failures(self, client, places, make_suspension, outbox, db):
        subscribe(client, "angeles-city")
        sub = db.query(Subscription).one()
        sub.failure_count = 3
        db.commit()
        s = make_suspension(places["angeles-city"], status=Status.PENDING)

        verify(client, s.id)

        assert sub.failure_count == 0
        assert sub.last_sent_at is not None

    def test_inactive_subscriptions_are_skipped(self, client, places, make_suspension, outbox):
        token = subscribe(client, "angeles-city").json()["manage_token"]
        client.delete(f"/api/v1/alerts/{token}")
        s = make_suspension(places["angeles-city"], status=Status.PENDING)

        verify(client, s.id)

        assert outbox.requests == []


class TestFacebook:
    @pytest.fixture
    def suspension(self, places, make_suspension) -> Suspension:
        return make_suspension(
            places["angeles-city"], status=Status.PENDING, levels=(Level.COLLEGE,),
            reason_detail="Signal No. 2",
        )

    def test_off_without_settings(self, db, suspension):
        assert notifications.plan(db, suspension, TODAY, Settings()) == []

    def test_posts_to_page_when_configured(self, db, suspension):
        settings = Settings(
            facebook_page_id="111", facebook_page_access_token="secret-token",
            public_base_url="https://walangpasok.example/",
        )

        [delivery] = notifications.plan(db, suspension, TODAY, settings)

        assert delivery.url == "https://graph.facebook.com/v23.0/111/feed"
        assert delivery.form is True
        assert delivery.payload["access_token"] == "secret-token"
        assert delivery.payload["link"] == "https://walangpasok.example/?q=angeles-city"
        message = delivery.payload["message"]
        assert message.startswith("WALANG PASOK: Angeles City")
        assert "College" in message and "Signal No. 2" in message
        assert suspension.source_url in message

    def test_facebook_failure_does_not_touch_subscriptions(self, db, suspension, outbox):
        outbox.next_status = 400
        settings = Settings(facebook_page_id="111", facebook_page_access_token="t")
        deliveries = notifications.plan(db, suspension, TODAY, settings)

        report = notifications.send_all(deliveries, lambda: pytest.fail("no session needed"), settings)

        assert (report.sent, report.failed) == (0, 1)
        assert outbox.requests[0]["content_type"] == "application/x-www-form-urlencoded"


def test_matches_rules(places, db):
    sub = Subscription(location=places["pampanga"])

    def s(loc: Location) -> Suspension:
        return Suspension(location=loc, date=TODAY + dt.timedelta(days=1))

    assert notifications.matches(sub, s(places["san-fernando"]))  # inside the province
    assert notifications.matches(sub, s(places["central-luzon"]))  # covers the province
    assert not notifications.matches(sub, s(places["angeles-city"]))  # independent city
    assert not notifications.matches(sub, s(places["quezon-city"]))
