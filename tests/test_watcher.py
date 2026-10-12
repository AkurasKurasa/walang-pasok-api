"""The watcher, run against Inquirer's real feed format and article pages.

The fixtures in tests/fixtures/ copy the live pages' structure (RSS items,
the #article_content element with its ads and "featured stories" in between)
and their suspension lists. Nothing here touches the network.
"""

import datetime as dt
from pathlib import Path

import pytest

from app.config import Settings
from app.models import Mode, Status, Suspension, WatchedPage
from app.services import watcher
from scripts.seed import upsert_locations

FIXTURES = Path(__file__).parent / "fixtures"
FEED = "https://newsinfo.inquirer.net/tag/walang-pasok/feed"
SEPT_10 = "https://newsinfo.inquirer.net/2301928/walang-pasok-class-suspensions-on-thursday-sept-10"
OCT_5 = "https://newsinfo.inquirer.net/2316727/in-person-class-suspensions-on-oct-5-in-line-of-national-teachers-day-celebration"
NIGHT_BEFORE_SEPT_10 = dt.date(2026, 9, 9)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeWeb:
    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise watcher.FetchError(f"{url}: 404")
        return self.pages[url]


@pytest.fixture
def seeded(db):
    """Every PSGC place, so same-named towns (Burgos, San Carlos City) must be told apart."""
    upsert_locations(db)
    return db


@pytest.fixture
def web():
    return FakeWeb({
        FEED: fixture("inquirer_feed.xml"),
        SEPT_10: fixture("inquirer_2026-09-10.html"),
        OCT_5: fixture("inquirer_2026-10-05.html"),
    })


def run(db, web, today=NIGHT_BEFORE_SEPT_10):
    return watcher.run_once(db, web, today=today)


def article(report, url):
    return next(a for a in report.articles if a.url == url)


def test_reads_the_rss_feed(web):
    listings = watcher.read_index(fixture("inquirer_feed.xml"), FEED, watcher.SOURCES[0])

    assert [item.url for item in listings] == [OCT_5, SEPT_10]
    assert listings[1].title == "WALANG PASOK: Class suspensions on Thursday, Sept. 10"
    assert listings[1].published == dt.datetime(2026, 9, 9, 0, 45, tzinfo=dt.UTC)


def test_reads_an_html_listing_too():
    source = watcher.WatchSource("X", "https://news.example/tag", r"^https://news\.example/\d+/")
    html = """<a href="/1/walang-pasok-monday">Walang pasok Monday</a> <a href="/2/senate">Senate</a>
              <a href="https://elsewhere.example/3/walang-pasok">Elsewhere</a>"""

    assert [i.url for i in watcher.read_index(html, source.index_url, source)] == ["https://news.example/1/walang-pasok-monday"]


def test_imports_the_real_sept_10_article(seeded, web):
    report = run(seeded, web)

    sept = article(report, SEPT_10)
    assert sept.status == "imported" and sept.date == dt.date(2026, 9, 10)
    assert [c.location.slug for c in sept.items] == [
        "san-simon-pampanga", "santo-tomas-pampanga", "masantol", "kibungan",
    ]
    assert sept.unmatched == []

    s = seeded.query(Suspension).filter(Suspension.location.has(slug="san-simon-pampanga")).one()
    assert s.status is Status.PENDING  # nothing goes public without a person
    assert s.mode is Mode.FACE_TO_FACE_ONLY
    assert s.lifted_on == dt.date(2026, 9, 12)  # until Friday, Sept. 11
    assert s.proof[0].url == SEPT_10 and s.proof[0].name == "Inquirer.net"
    assert s.proof[0].excerpt.startswith("San Simon – no face-to-face classes")
    assert s.proof[0].posted_at is not None


def test_ignores_text_outside_the_article(seeded, web):
    report = run(seeded, web)

    slugs = [c.location.slug for c in article(report, SEPT_10).items]
    # The sidebar's Angeles City line and the footer's Quezon City line are not the article.
    assert "angeles-city" not in slugs and "quezon-city" not in slugs
    # Malacañang's list of provinces under alternative arrangements isn't a suspension list.
    assert "metro-manila" not in slugs and "pampanga" not in slugs


def test_in_person_headline_applies_to_every_line(seeded, web):
    report = run(seeded, web)

    items = article(report, OCT_5).items
    assert {c.mode for c in items} == {Mode.FACE_TO_FACE_ONLY}
    sta_rita = next(c for c in items if c.location.slug == "santa-rita-pampanga")
    assert sta_rita.mode is Mode.FACE_TO_FACE_ONLY  # "In-person classes suspended Oct. 5 ..."
    assert sta_rita.reason.value == "event"  # Teachers' Day


def test_old_articles_are_remembered_not_imported(seeded, web):
    report = run(seeded, web, today=dt.date(2026, 10, 11))

    assert {a.status for a in report.articles} == {"old"}
    assert seeded.query(Suspension).count() == 0
    assert seeded.query(WatchedPage).count() == 2


def test_second_run_does_nothing(seeded, web):
    run(seeded, web)
    second = run(seeded, web)

    assert {a.status for a in second.articles} == {"unchanged"}
    assert second.new_items == []
    assert seeded.query(Suspension).count() == 11  # 4 on Sept. 10, 7 on Oct. 5


def test_correction_flags_a_dropped_place(seeded, web, outbox):
    """Inquirer's Oct. 5, 2026 list first included Angeles City, then an erratum removed it."""
    day_before = dt.date(2026, 10, 4)
    web.pages[OCT_5] = fixture("inquirer_2026-10-05_before_erratum.html")
    first = run(seeded, web, today=day_before)
    assert {"angeles-city", "santa-rita-pampanga"} <= {c.location.slug for c in article(first, OCT_5).items}

    web.pages[OCT_5] = fixture("inquirer_2026-10-05.html")
    second = run(seeded, web, today=day_before)

    assert [w.location_name for w in second.withdrawn] == ["Angeles City"]
    summary = watcher.admin_summary(second, Settings())["content"]
    assert "Angeles City" in summary and "no longer listed" in summary


def test_new_source_for_known_suspension_becomes_proof(seeded, web):
    run(seeded, web)
    other = "https://newsinfo.inquirer.net/2301999/walang-pasok-update-sept-10"
    web.pages[FEED] = fixture("inquirer_feed.xml").replace(SEPT_10, other)
    web.pages[other] = fixture("inquirer_2026-09-10.html")

    report = run(seeded, web)

    assert {c.action for c in article(report, other).items} == {"added_source"}
    masantol = seeded.query(Suspension).filter(Suspension.location.has(slug="masantol")).one()
    assert [p.url for p in masantol.proof] == [SEPT_10, other]


def test_unreachable_feed_is_an_error(seeded):
    report = run(seeded, FakeWeb({}))

    assert report.articles == [] and len(report.errors) == len(watcher.SOURCES)  # one per feed


def test_admin_summary(seeded, web):
    report = run(seeded, web)
    payload = watcher.admin_summary(report, Settings(public_base_url="https://wp.example"))

    assert payload["content"].startswith("**11 new pending suspension(s)")
    assert "San Simon" in payload["content"] and "https://wp.example/docs" in payload["content"]
    assert "⚠" in payload["content"]  # San Simon's exception and until-date notes
    assert payload["allowed_mentions"] == {"parse": []}
    assert watcher.admin_summary(watcher.WatchReport(), Settings()) is None


def test_notify_admin_posts_when_configured(seeded, web, outbox):
    report = run(seeded, web)

    assert watcher.notify_admin(report, Settings()) is False
    assert watcher.notify_admin(report, Settings(admin_discord_webhook_url="https://discord.com/api/webhooks/1/x")) is True
    assert len(outbox.requests) == 1


def test_robots_txt_is_respected(monkeypatch):
    fetcher = watcher.PoliteFetcher(delay=0)
    monkeypatch.setattr(fetcher, "_get", lambda url: "User-agent: *\nDisallow: /private/" if url.endswith("robots.txt") else "ok")

    assert fetcher("https://site.example/news/1") == "ok"
    with pytest.raises(watcher.FetchError, match="robots.txt"):
        fetcher("https://site.example/private/1")


def test_unreadable_current_article_is_flagged_for_a_person(seeded, web):
    """A site that blocks robots must not make its places silently disappear."""
    del web.pages[SEPT_10]

    report = run(seeded, web)

    assert [a.url for a in report.unreadable] == [SEPT_10]
    assert report.unreadable[0].date == dt.date(2026, 9, 10)
    summary = watcher.admin_summary(report, Settings())["content"]
    assert "Couldn't read" in summary and SEPT_10 in summary


def test_unreadable_old_article_is_not_flagged(seeded, web):
    del web.pages[OCT_5]

    report = run(seeded, web, today=dt.date(2026, 10, 11))

    assert report.unreadable == []


def test_rate_limited_fetch_waits_and_retries(monkeypatch):
    import email.message
    import urllib.error

    calls, sleeps = [], []
    headers = email.message.Message()
    headers["Retry-After"] = "3"

    class Response:
        headers = email.message.Message()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"ok"

    def urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", headers, None)
        return Response()

    monkeypatch.setattr(watcher.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(watcher.time, "sleep", sleeps.append)

    assert watcher.PoliteFetcher(delay=0)._get("https://blog.example/x") == "ok"
    assert len(calls) == 2 and 3.0 in sleeps
