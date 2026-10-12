"""Hands-off mode: well-sourced finds go live and alert subscribers on their own;
everything else waits for a person."""

import datetime as dt
from contextlib import nullcontext
from pathlib import Path

import pytest

from app.config import Settings
from app.models import Channel, Location, SourceKind, Status, Subscription, Suspension, SuspensionSource
from app.services import autopilot, watcher
from scripts.seed import upsert_locations

FIXTURES = Path(__file__).parent / "fixtures"
GMA = next(s for s in watcher.SOURCES if s.name == "GMA News")
GMA_URL = "https://www.gmanetwork.com/news/serbisyopubliko/walangpasok/1005549/walang-pasok-class-suspensions-for-monday-october-12-2026/story/"
NIGHT_BEFORE = dt.date(2026, 10, 11)


@pytest.fixture
def seeded(db):
    upsert_locations(db)
    return db


def fake_web(pages: dict[str, str]):
    def fetch(url):
        if url not in pages:
            raise watcher.FetchError(f"{url}: 404")
        return pages[url]

    return fetch


def feed(*items: tuple[str, str]) -> str:
    entries = "".join(f"<item><title>{t}</title><link>{u}</link></item>" for t, u in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{entries}</channel></rss>'


def cycle(db, pages, sources, settings=None):
    watcher_sources = watcher.SOURCES
    try:
        watcher.SOURCES = sources
        return autopilot.run_cycle(
            fetch=fake_web(pages), today=NIGHT_BEFORE,
            settings=settings or Settings(), session_factory=lambda: nullcontext(db),
        )
    finally:
        watcher.SOURCES = watcher_sources


def gma_pages():
    return {
        GMA.index_url: feed(("WALANG PASOK: Class suspensions for Monday, October 12, 2026", GMA_URL)),
        GMA_URL: (FIXTURES / "gma_2026-10-12.html").read_text(encoding="utf-8"),
    }


def follow(db, slug: str) -> Subscription:
    sub = Subscription(
        channel=Channel.DISCORD_WEBHOOK, target="https://discord.com/api/webhooks/1/abc",
        location=db.query(Location).filter_by(slug=slug).one(), manage_token="t", active=True,
    )
    db.add(sub)
    db.commit()
    return sub


def test_lgu_post_link_publishes_and_alerts_on_its_own(seeded, outbox):
    follow(seeded, "san-jose-del-monte-city")

    result = cycle(seeded, gma_pages(), [GMA])

    s = seeded.query(Suspension).one()
    assert s.status is Status.VERIFIED and s.verified_at is not None
    assert result.published == {s.id: "links to the LGU's own post"}
    discord = [r for r in outbox.requests if "discord.com/api/webhooks/1/" in r["url"]]
    assert len(discord) == 1  # the follower heard about it without anyone pressing verify


def test_single_news_report_waits_for_a_person(seeded, outbox):
    source = watcher.WatchSource("Example News", "https://news.example/feed", r"^https://news\.example/\d+")
    pages = {
        source.index_url: feed(("Walang pasok on Monday, October 12", "https://news.example/1")),
        "https://news.example/1": "<p>Walang pasok on Monday, October 12</p><p>Masantol, Pampanga - All levels</p>",
    }

    result = cycle(seeded, pages, [source])

    s = seeded.query(Suspension).one()
    assert s.status is Status.PENDING
    assert "only one news report" in result.held[s.id]
    assert outbox.requests == []


def test_second_outlet_publishes_a_held_suspension(seeded):
    one = watcher.WatchSource("Example News", "https://news.example/feed", r"^https://news\.example/\d+")
    two = watcher.WatchSource("Other Paper", "https://paper.example/feed", r"^https://paper\.example/\d+")
    article = "<p>Walang pasok on Monday, October 12</p><p>Masantol, Pampanga - All levels</p>"
    pages = {
        one.index_url: feed(("Walang pasok on Monday, October 12", "https://news.example/1")),
        "https://news.example/1": article,
        two.index_url: feed(("Walang pasok on Monday, October 12", "https://paper.example/9")),
        "https://paper.example/9": article,
    }

    first = cycle(seeded, {k: v for k, v in pages.items() if "paper" not in k}, [one])
    second = cycle(seeded, pages, [one, two])

    s = seeded.query(Suspension).one()
    assert first.published == {}
    assert second.published == {s.id: "reported by 2 news outlets"}
    assert s.status is Status.VERIFIED


def test_warnings_keep_it_pending_even_with_an_lgu_link(seeded):
    source = watcher.WatchSource("Example News", "https://news.example/feed", r"^https://news\.example/\d+")
    pages = {
        source.index_url: feed(("Walang pasok on Monday, October 12", "https://news.example/1")),
        "https://news.example/1": (
            "<p>Walang pasok on Monday, October 12</p>"
            '<p><a href="https://www.facebook.com/SanSimonLGU/posts/1">San Simon, Pampanga</a>'
            " - All levels (except Concepcion Integrated School)</p>"
        ),
    }

    result = cycle(seeded, pages, [source])

    s = seeded.query(Suspension).one()
    assert s.status is Status.PENDING
    assert "exceptions" in result.held[s.id]


def test_auto_publish_can_be_turned_off(seeded):
    result = cycle(seeded, gma_pages(), [GMA], settings=Settings(auto_publish=False))

    assert seeded.query(Suspension).one().status is Status.PENDING
    assert list(result.held.values()) == ["auto-publishing is off"]


def test_trust_reason():
    s = Suspension(source_url="https://news.example/1", source_name="A", source_kind=SourceKind.NEWS_ARTICLE)
    assert autopilot.trust_reason(s) is None
    s.extra_sources = [SuspensionSource(kind=SourceKind.NEWS_ARTICLE, name="B", url="https://www.news.example/2")]
    assert autopilot.trust_reason(s) is None  # same outlet twice
    s.extra_sources.append(SuspensionSource(kind=SourceKind.NEWS_ARTICLE, name="C", url="https://paper.example/3"))
    assert autopilot.trust_reason(s) == "reported by 2 news outlets"
    s.extra_sources.append(SuspensionSource(kind=SourceKind.FACEBOOK_POST, name="LGU", url="https://www.facebook.com/x"))
    assert autopilot.trust_reason(s) == "links to the LGU's own post"


def test_background_watcher_is_off_in_tests_and_obeys_the_setting():
    assert autopilot.start_background(Settings(watcher_enabled=False)) is None


def test_background_watcher_runs_cycles(monkeypatch):
    ran = []
    monkeypatch.setattr(autopilot, "run_cycle", lambda: ran.append(1))
    worker = autopilot.BackgroundWatcher(minutes=0.001, first_delay=0)
    worker.start()
    for _ in range(200):
        if len(ran) >= 2:
            break
        import time
        time.sleep(0.01)
    worker.stop()
    assert len(ran) >= 2
