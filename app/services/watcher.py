"""The news watcher: check suspension news pages, import anything new as pending.

One run (`run_once`) does this for each source:

1. Fetch the source's index page (e.g. Inquirer's "walang pasok" tag page).
2. Pick out links to suspension articles.
3. For each article not seen before (or changed since): read the date from its
   headline, parse the suspension lines, and save them as **pending**, or add
   the article as extra proof to suspensions already on file.
4. Remember every page in `watched_pages`, so the next run only does new work.

Run it every 10-15 minutes (GitHub Actions in production, `--loop` locally).
New finds are reported to ADMIN_DISCORD_WEBHOOK_URL so a person can verify
them. Verifying is what sends the public alerts.

Being a polite robot: it identifies itself, obeys robots.txt, waits between
requests and only fetches a handful of articles per source per run.
"""

import datetime as dt
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Reason, SourceKind, Status, Suspension, SuspensionSource, WatchedPage
from app.services import clock, importer, notifications

log = logging.getLogger(__name__)

USER_AGENT = notifications.USER_AGENT
MAX_ARTICLES_PER_SOURCE = 12
POLITE_DELAY_SECONDS = 1.0


@dataclass(frozen=True)
class WatchSource:
    name: str  # shown as the source of the proof, e.g. "Inquirer.net"
    index_url: str  # an RSS feed or a page listing suspension articles, newest first
    link_pattern: str  # regex an article URL must match
    link_text_pattern: str = r"pasok|suspen|no classes|class(?:es)? cancel"
    kind: SourceKind = SourceKind.NEWS_ARTICLE
    # The element holding the article body, so sidebars are ignored: an id, or
    # "[attr=value]" such as "[itemprop=articleBody]".
    content_id: str | None = None
    # Read the body from the page's schema.org NewsArticle data (JSON-LD
    # "articleBody"), for sites that fill the visible article in with JavaScript.
    ld_json_body: bool = False


# Add sources here. Before adding one, check that its robots.txt allows it and
# that the article text is in the HTML (or its JSON-LD), not loaded by JavaScript.
# Sites that refuse automated readers (403, or robots.txt) are left out on purpose.
SOURCES: list[WatchSource] = [
    WatchSource(
        name="Inquirer.net",
        # The tag's RSS feed: made for automated readers, and has publish times.
        index_url="https://newsinfo.inquirer.net/tag/walang-pasok/feed",
        link_pattern=r"^https://newsinfo\.inquirer\.net/\d+/[\w-]+/?$",
        content_id="article_content",
    ),
    WatchSource(
        name="GMA News",
        # Serbisyo Publiko feed; its "WALANG PASOK" lists link each place to the LGU's own post.
        index_url="https://data.gmanetwork.com/gno/rss/serbisyopubliko/feed.xml",
        link_pattern=r"^https://www\.gmanetwork\.com/news/serbisyopubliko/walangpasok/\d+/[\w-]+/story/?$",
        ld_json_body=True,
    ),
    WatchSource(
        name="Guro Tayo",
        # A teachers' site that rounds up LGU and DepEd announcements, with links to each post.
        index_url="https://www.gurotayo.com/feeds/posts/default?alt=rss&max-results=25",
        link_pattern=r"^https://www\.gurotayo\.com/\d{4}/\d{2}/[\w-]+\.html$",
        link_text_pattern=r"pasok|suspen",
        content_id="[itemprop=articleBody]",
    ),
]


def ld_json_article_body(html: str) -> str | None:
    """The `articleBody` of a page's schema.org NewsArticle JSON-LD, if any."""
    for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and isinstance(item.get("articleBody"), str):
                return item["articleBody"]
    return None


def read_article(html: str, source: WatchSource) -> importer.Page:
    """The article's title, body text and links, without sidebars."""
    if source.ld_json_body:
        body = ld_json_article_body(html)
        if body:
            page = importer.html_to_page(body)
            page.title = importer.html_to_page(html).title
            return page
    return importer.html_to_page(html, source.content_id)


@dataclass
class Listing:
    url: str
    title: str
    published: dt.datetime | None = None


def read_index(body: str, base_url: str, source: WatchSource) -> list[Listing]:
    """Article links from an RSS feed or an HTML page, newest first."""
    if body.lstrip().startswith("<?xml") or "<rss" in body[:500]:
        items = []
        for item in ET.fromstring(body.encode()).iter("item"):
            link, title = (item.findtext("link") or "").strip(), (item.findtext("title") or "").strip()
            published = None
            if item.findtext("pubDate"):
                try:
                    published = parsedate_to_datetime(item.findtext("pubDate"))
                except (TypeError, ValueError):
                    published = None
            items.append(Listing(link, title, published))
    else:
        page = importer.html_to_page(body)
        items = [Listing(urllib.parse.urljoin(base_url, href).split("#")[0], text) for href, text in page.links]

    found: list[Listing] = []
    for item in items:
        if not re.match(source.link_pattern, item.url):
            continue
        if not re.search(source.link_text_pattern, f"{item.title} {item.url}", re.I):
            continue
        if item.url not in {f.url for f in found}:
            found.append(item)
    return found[:MAX_ARTICLES_PER_SOURCE]


# --- Fetching ----------------------------------------------------------------


class FetchError(Exception):
    pass


Fetcher = Callable[[str], str]


def _retry_after(header: str | None, default: float = 20.0, cap: float = 60.0) -> float:
    """Seconds to wait from a Retry-After header (seconds form), within reason."""
    try:
        return min(max(float(header), 1.0), cap) if header else default
    except ValueError:
        return default


class PoliteFetcher:
    """Fetches pages with an honest User-Agent, obeying each site's robots.txt."""

    def __init__(self, timeout: float = 20.0, delay: float = POLITE_DELAY_SECONDS) -> None:
        self.timeout = timeout
        self.delay = delay
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last = 0.0

    def _allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.parse(self._get(f"{root}/robots.txt").splitlines())
                self._robots[root] = rp
            except FetchError:
                self._robots[root] = None  # no readable robots.txt: allowed
        rp = self._robots[root]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def _get(self, url: str, retries: int = 2) -> str:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,fil"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read(3_000_000).decode(charset, "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and retries > 0:
                # Rate-limited (common from shared cloud IPs like GitHub's): wait as asked, then retry.
                time.sleep(_retry_after(exc.headers.get("Retry-After")))
                return self._get(url, retries - 1)
            raise FetchError(f"{url}: {exc}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"{url}: {exc}") from exc

    def __call__(self, url: str) -> str:
        if not self._allowed(url):
            raise FetchError(f"{url}: blocked by the site's robots.txt")
        return self._get(url)


# --- One run -----------------------------------------------------------------


@dataclass
class ArticleReport:
    url: str
    title: str
    date: dt.date | None
    status: str  # "unchanged", "old", "no_date", "imported", "error"
    items: list[importer.Candidate] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class Withdrawn:
    """A suspension whose source article was updated and no longer lists it."""

    suspension_id: int
    location_name: str
    url: str
    status: Status


@dataclass
class WatchReport:
    articles: list[ArticleReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    withdrawn: list[Withdrawn] = field(default_factory=list)
    unreadable: list[ArticleReport] = field(default_factory=list)  # current lists we couldn't open

    @property
    def new_items(self) -> list[importer.Candidate]:
        return [c for a in self.articles for c in a.items if c.action == "created"]

    @property
    def new_proof(self) -> list[importer.Candidate]:
        return [c for a in self.articles for c in a.items if c.action == "added_source"]


def headline_date(page: importer.Page, today: dt.date) -> dt.date | None:
    """The suspended day, from the headline ("...on Thursday, Sept. 10")."""
    for text in (page.title, *page.text.splitlines()[:40]):
        if re.search(r"pasok|suspen|class", text, re.I):
            found = importer.find_date(text, today)
            if found:
                return found
    return None


def _from_article(db: Session, url: str, day: dt.date) -> list[Suspension]:
    """Suspensions on `day` that cite `url`, as their main source or extra proof."""
    main = db.scalars(select(Suspension).where(Suspension.source_url == url, Suspension.date == day)).all()
    extra = db.scalars(
        select(Suspension)
        .join(SuspensionSource, SuspensionSource.suspension_id == Suspension.id)
        .where(SuspensionSource.url == url, Suspension.date == day)
    ).all()
    return list({s.id: s for s in [*main, *extra]}.values())


def run_once(
    db: Session,
    fetch: Fetcher,
    today: dt.date | None = None,
    sources: list[WatchSource] | None = None,
) -> WatchReport:
    today = today or clock.today()
    report = WatchReport()
    index = importer.PlaceIndex.from_db(db)

    for source in sources or SOURCES:
        try:
            listings = read_index(fetch(source.index_url), source.index_url, source)
        except (FetchError, ET.ParseError) as exc:
            report.errors.append(f"{source.index_url}: {exc}")
            continue

        for listing in listings:
            url = listing.url
            seen = db.scalar(select(WatchedPage).where(WatchedPage.url == url))
            try:
                page = read_article(fetch(url), source)
            except FetchError as exc:
                near = listing.published.date() if listing.published else today
                day = headline_date(importer.Page(listing.title, "", []), near)
                report.articles.append(ArticleReport(url, listing.title, day, "error", error=str(exc)))
                if day is not None and day >= today:
                    # A current list we couldn't read (e.g. the site blocks robots): a person
                    # must check it, or its places are silently missed.
                    report.unreadable.append(ArticleReport(url, listing.title, day, "error", error=str(exc)))
                if seen:
                    seen.last_error, seen.last_checked_at = str(exc)[:1000], clock.now()
                continue

            digest = hashlib.sha256(page.text.encode()).hexdigest()
            title = (page.title or listing.title)[:300]
            if seen is None:
                seen = WatchedPage(url=url, content_hash="", title=title, found=0)
                db.add(seen)
            seen.last_checked_at, seen.last_error = clock.now(), None
            if seen.content_hash == digest:
                report.articles.append(ArticleReport(url, title, None, "unchanged"))
                continue
            was_seen_before = bool(seen.content_hash)
            seen.content_hash, seen.title, seen.last_changed_at = digest, title, clock.now()

            near = listing.published.date() if listing.published else today
            day = headline_date(page, near) or headline_date(importer.Page(listing.title, "", []), near)
            if day is None:
                report.articles.append(ArticleReport(url, title, None, "no_date"))
                continue
            if day < today:
                # Old news: remember it, don't import it.
                report.articles.append(ArticleReport(url, title, day, "old"))
                continue

            result = importer.parse_text(
                index, page.text, day,
                importer.parse_reason(title, Reason.WEATHER),
                importer.article_mode(title, "\n".join(page.text.splitlines()[:30])),
            )
            if not result.candidates:
                # Roundups written as numbered sections with a paragraph each (Guro Tayo).
                sections = importer.parse_sections(
                    index, page.text, day, importer.parse_reason(title, Reason.WEATHER),
                    importer.article_mode(title),
                )
                if sections.candidates or sections.unmatched:
                    result = sections
            importer.attach_official_links(result, page.links)
            posted = listing.published
            importer.import_parsed(
                db, result, day, importer.SourceInfo(url=url, name=source.name, kind=source.kind, posted_at=posted)
            )
            seen.found = len(result.candidates)
            report.articles.append(
                ArticleReport(url, title, day, "imported", result.candidates, result.unmatched)
            )

            if was_seen_before:
                # The article was edited. Anything it used to list but no longer
                # does goes to a person: news sites correct their lists (errata).
                still_listed = {c.location.id for c in result.candidates}
                for s in _from_article(db, url, day):
                    if s.location_id not in still_listed:
                        report.withdrawn.append(Withdrawn(s.id, s.location.name, url, s.status))
        db.commit()
    return report


# --- Telling the admin -------------------------------------------------------


def admin_summary(
    report: WatchReport,
    settings: Settings,
    published: dict[int, str] | None = None,
    held: dict[int, str] | None = None,
) -> dict | None:
    """A Discord message about new finds, or None if there's nothing new.

    `published` / `held` (suspension id -> reason) come from the autopilot: what
    went live on its own, and what still needs a person.
    """
    published, held = published or {}, held or {}
    new, proof, gone = report.new_items, report.new_proof, report.withdrawn
    blocked = report.unreadable
    if not new and not proof and not gone and not published and not blocked:
        return None
    base = settings.public_base_url.rstrip("/")
    lines = []
    for c in new[:20]:
        note = f" ⚠ {' '.join(c.notes)}" if c.notes else ""
        if c.suspension_id in published:
            status = f"✅ published ({published[c.suspension_id]})"
        elif c.suspension_id in held:
            status = f"⏳ needs review ({held[c.suspension_id]})"
        else:
            status = ""
        lines.append(f"• **#{c.suspension_id} {c.location.name}** {status}: {c.line[:150]}{note}")
    if len(new) > 20:
        lines.append(f"…and {len(new) - 20} more.")
    later = [sid for sid in published if sid not in {c.suspension_id for c in new}]
    if later:
        lines.append(f"✅ Published after more proof came in: {', '.join(f'#{sid}' for sid in later)}")
    if proof:
        lines.append(f"Also added {len(proof)} news source(s) as proof to suspensions already on file.")
    for w in gone:
        lines.append(
            f"⚠ **#{w.suspension_id} {w.location_name}** ({w.status.value}) is no longer listed in its "
            f"source, which was updated: {w.url} Check it, and correct it if the source was right."
        )
    for a in blocked[:5]:
        lines.append(
            f"⚠ Couldn't read **{a.title[:100]}** ({a.date}): {a.url} Places it lists may be missing. "
            "Check it and add them (POST /api/v1/suspensions or scripts.import_article --file)."
        )
    lines.append(f"Review: {base}/docs#/suspensions (PATCH /api/v1/suspensions/{{id}}/verify)")
    if new and published:
        heading = f"**{len(new)} new suspension(s) found, {len(published)} published automatically**"
    elif new:
        heading = f"**{len(new)} new pending suspension(s) found by the watcher**"
    else:
        heading = "**Watcher update**"
    content = heading + "\n" + "\n".join(lines)
    return {"username": "Walang Pasok watcher", "allowed_mentions": {"parse": []}, "content": content[:1990]}


def notify_admin(
    report: WatchReport,
    settings: Settings | None = None,
    published: dict[int, str] | None = None,
    held: dict[int, str] | None = None,
) -> bool:
    settings = settings or get_settings()
    payload = admin_summary(report, settings, published, held)
    if payload is None or not settings.admin_discord_webhook_url:
        return False
    result = notifications.post_json(settings.admin_discord_webhook_url, payload, settings.notify_timeout)
    if not result.ok:
        log.warning("Admin notification failed: %s", result.error)
    return result.ok
