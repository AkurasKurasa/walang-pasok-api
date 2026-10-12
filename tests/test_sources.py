"""More sources: GMA News (body in JSON-LD), Guro Tayo (Blogger), and LGU posts
credited as the official source when a news list links to them.

tests/fixtures/gma_2026-10-12.html is GMA's real "Class suspensions for Monday,
October 12, 2026" page as served on 2026-10-12.
"""

import datetime as dt
from pathlib import Path

import pytest

from app.models import Level, Reason, SourceKind, Status, Suspension
from app.services import importer, watcher
from scripts.seed import upsert_locations

FIXTURES = Path(__file__).parent / "fixtures"
GMA = next(s for s in watcher.SOURCES if s.name == "GMA News")
GURO_TAYO = next(s for s in watcher.SOURCES if s.name == "Guro Tayo")
GMA_URL = "https://www.gmanetwork.com/news/serbisyopubliko/walangpasok/1005549/walang-pasok-class-suspensions-for-monday-october-12-2026/story/"
OCT_12 = dt.date(2026, 10, 12)


@pytest.fixture
def seeded(db):
    upsert_locations(db)
    return db


def gma_web():
    feed = f"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>WALANG PASOK: Class suspensions for Monday, October 12, 2026</title>
        <link>{GMA_URL}</link><pubDate>Sun, 11 Oct 2026 21:52:20 +0800</pubDate></item>
      <item><title>Senate approves budget</title>
        <link>https://www.gmanetwork.com/news/topstories/nation/1005550/senate/story/</link></item>
    </channel></rss>"""
    pages = {GMA.index_url: feed, GMA_URL: (FIXTURES / "gma_2026-10-12.html").read_text(encoding="utf-8")}

    def fetch(url):
        if url not in pages:
            raise watcher.FetchError(f"{url}: 404")
        return pages[url]

    return fetch


def test_gma_body_comes_from_json_ld():
    page = watcher.read_article((FIXTURES / "gma_2026-10-12.html").read_text(encoding="utf-8"), GMA)

    assert page.title.startswith("WALANG PASOK: Class suspensions for Monday, October 12, 2026")
    assert page.text.startswith("Classes are suspended in some areas")
    assert "San Jose del Monte City, Bulacan - All levels" in page.text


def test_gma_list_is_imported_with_the_lgu_post_as_official_source(seeded):
    report = watcher.run_once(seeded, gma_web(), today=dt.date(2026, 10, 11), sources=[GMA])

    [article] = report.articles  # the Senate story isn't a suspension
    assert article.status == "imported" and article.date == OCT_12
    s = seeded.query(Suspension).one()
    assert s.location.slug == "san-jose-del-monte-city"
    assert s.status is Status.PENDING
    assert s.levels == [Level.ALL]
    assert s.reason is Reason.OTHER  # water supply interruption, not weather
    official, news = s.proof
    assert official.kind is SourceKind.FACEBOOK_POST and official.url.startswith("https://www.facebook.com/photo?fbid=")
    assert official.primary
    assert news.url == GMA_URL and news.name == "GMA News"
    assert news.excerpt.startswith("San Jose del Monte City, Bulacan")


def test_school_by_school_lists_are_left_to_a_person(seeded):
    """Imus lists individual schools per barangay: not a citywide suspension."""
    report = watcher.run_once(seeded, gma_web(), today=dt.date(2026, 10, 11), sources=[GMA])

    assert not any(c.location.slug == "imus-city" for c in report.articles[0].items)
    assert any("Barangay" in u for u in report.articles[0].unmatched)


def test_official_links_only_count_for_lgu_channels(seeded):
    index = importer.PlaceIndex.from_db(seeded)
    text = "Angeles City, Pampanga - All levels\nMasantol, Pampanga - All levels"
    result = importer.parse_text(index, text, OCT_12, Reason.EVENT)
    importer.attach_official_links(result, [
        ("https://www.facebook.com/AngelesCityGov/posts/1", "Angeles City, Pampanga"),
        ("https://someblog.example/masantol", "Masantol, Pampanga"),  # not an LGU channel
    ])

    official = {c.location.slug: c.official_url for c in result.candidates}
    assert official == {"angeles-city": "https://www.facebook.com/AngelesCityGov/posts/1", "masantol": None}


@pytest.mark.parametrize(("url", "official"), [
    ("https://www.facebook.com/photo?fbid=1", True),
    ("https://m.facebook.com/story.php?id=1", True),
    ("https://x.com/QCGov/status/1", True),
    ("https://quezoncity.gov.ph/advisory", True),
    ("https://www.deped.gov.ph/2026/10/advisory", True),
    ("https://notgov.ph.example.com/", False),
    ("https://www.gmanetwork.com/news/", False),
])
def test_is_official_link(url, official):
    assert importer.is_official_link(url) is official


def test_guro_tayo_reads_the_blogger_post_body():
    html = """<html><head><title>WALANG PASOK: Monday</title></head><body>
      <div class="sidebar">Angeles City, Pampanga - All levels</div>
      <div class='post-body entry-content' id='post-body-123' itemprop='articleBody'>
        <p><a href="https://www.facebook.com/photo?fbid=9">Mamburao, Occidental Mindoro</a> - All levels, public and private</p>
      </div></body></html>"""

    page = watcher.read_article(html, GURO_TAYO)

    assert "Mamburao" in page.text and "Angeles" not in page.text
    assert page.links == [("https://www.facebook.com/photo?fbid=9", "Mamburao, Occidental Mindoro")]


@pytest.mark.parametrize(("text", "reason"), [
    ("due to water supply interruption", Reason.OTHER),
    ("feast of La Naval", Reason.EVENT),
    ("poor air quality", Reason.OTHER),
    ("Tropical Storm Signal No. 2", Reason.WEATHER),
])
def test_reasons(text, reason):
    assert importer.parse_reason(text, Reason.WEATHER) is reason


def test_guro_tayo_roundup_sections(seeded):
    """Guro Tayo's real Oct. 12, 2026 roundup: numbered sections, one paragraph each."""
    html = (FIXTURES / "gurotayo_2026-10-12.html").read_text(encoding="utf-8")
    page = watcher.read_article(html, GURO_TAYO)
    result = importer.parse_sections(importer.PlaceIndex.from_db(seeded), page.text, OCT_12, Reason.EVENT)
    importer.attach_official_links(result, page.links)
    found = {c.location.slug: c for c in result.candidates}

    assert set(found) == {"san-jose-del-monte-city", "mamburao"}
    sjdm = found["san-jose-del-monte-city"]
    assert sjdm.levels == [Level.ALL] and sjdm.reason is Reason.OTHER  # water shutdown
    assert sjdm.until_lifted and sjdm.lifted_on == dt.date(2026, 10, 15)  # Oct. 12-14
    assert "Executive Order No. 60" in found["mamburao"].line  # "No." isn't a sentence end
    assert all(c.official_url.startswith("https://www.facebook.com/photo") for c in result.candidates)
    # Holiday-only proclamations, and Cauayan's "does not ... suspension of classes", go to a person.
    held = " ".join(result.unmatched)
    for town in ("Pamplona", "Talisay", "Pilar", "Cauayan"):
        assert town in held


def test_later_source_with_a_date_range_extends_the_suspension(seeded):
    """GMA lists San Jose del Monte for Oct. 12; Guro Tayo says Oct. 12-14."""
    report = watcher.run_once(seeded, gma_web(), today=dt.date(2026, 10, 11), sources=[GMA])
    sjdm = seeded.query(Suspension).one()
    assert not sjdm.until_lifted

    html = (FIXTURES / "gurotayo_2026-10-12.html").read_text(encoding="utf-8")
    page = watcher.read_article(html, GURO_TAYO)
    result = importer.parse_sections(importer.PlaceIndex.from_db(seeded), page.text, OCT_12, Reason.EVENT)
    importer.import_parsed(seeded, result, OCT_12, importer.SourceInfo(url="https://www.gurotayo.com/x.html", name="Guro Tayo"))

    seeded.refresh(sjdm)
    assert report.articles[0].status == "imported"
    assert sjdm.until_lifted and sjdm.lifted_on == dt.date(2026, 10, 15)
