"""Hands-off mode: watch the news, publish what's well-sourced, alert subscribers.

One cycle (`run_cycle`):

1. The watcher reads the news sources and saves new finds as pending.
2. Each find from this run is published automatically when it's well-sourced
   (`trust_reason`) and the importer raised no warnings about it.
3. Discord channels and the Facebook Page hear about what was published.
4. The admin channel gets a summary: what went live, and what needs a person.

The web app runs a cycle every WATCH_INTERVAL_MINUTES in a background thread
(`start_background`), and GitHub Actions runs `python -m scripts.watch`, which
does the same thing. Nobody has to run anything by hand.

What stays pending for a person: a single news report with no link to the
LGU's own post, lines with exceptions or unreadable dates, and places whose
source article later dropped them.
"""

import datetime as dt
import logging
import threading
import urllib.parse
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import session_scope
from app.models import SourceKind, Status, Suspension
from app.services import clock, notifications, watcher
from app.services import suspensions as suspension_service

log = logging.getLogger(__name__)

OFFICIAL_KINDS = {SourceKind.FACEBOOK_POST, SourceKind.X_POST, SourceKind.OFFICIAL_WEBSITE}


def _outlet(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or url
    return host.removeprefix("www.")


def trust_reason(suspension: Suspension) -> str | None:
    """Why this suspension is safe to publish without a person, or None."""
    if any(p.kind in OFFICIAL_KINDS for p in suspension.proof):
        return "links to the LGU's own post"
    outlets = {_outlet(p.url) for p in suspension.proof if p.kind is SourceKind.NEWS_ARTICLE}
    if len(outlets) >= 2:
        return f"reported by {len(outlets)} news outlets"
    return None


@dataclass
class CycleReport:
    watch: watcher.WatchReport
    published: dict[int, str] = field(default_factory=dict)  # suspension id -> why
    held: dict[int, str] = field(default_factory=dict)  # suspension id -> why not
    alerts: notifications.DispatchReport | None = None


def publish_trusted(
    db: Session, report: watcher.WatchReport, today: dt.date, settings: Settings
) -> tuple[dict[int, str], dict[int, str], list[notifications.Delivery]]:
    """Verify this run's well-sourced finds. Returns (published, held, alerts to send)."""
    published: dict[int, str] = {}
    held: dict[int, str] = {}
    deliveries: list[notifications.Delivery] = []
    withdrawn = {w.suspension_id for w in report.withdrawn}

    for c in [*report.new_items, *report.new_proof]:
        if c.suspension_id is None or c.suspension_id in published or c.suspension_id in held:
            continue
        s = db.get(Suspension, c.suspension_id)
        if s is None or s.status is Status.VERIFIED:
            continue
        if not settings.auto_publish:
            held[s.id] = "auto-publishing is off"
            continue
        if s.id in withdrawn:
            held[s.id] = "its source no longer lists it"
            continue
        if c.notes:
            held[s.id] = " ".join(c.notes)
            continue
        reason = trust_reason(s)
        if reason is None:
            held[s.id] = "only one news report and no link to the LGU's own post"
            continue
        s, newly = suspension_service.verify_with_change(db, s.id)
        if newly:
            published[s.id] = reason
            deliveries.extend(notifications.plan(db, s, today, settings))
    return published, held, deliveries


def run_cycle(
    fetch: watcher.Fetcher | None = None,
    today: dt.date | None = None,
    settings: Settings | None = None,
    session_factory: notifications.SessionFactory = session_scope,
) -> CycleReport:
    settings = settings or get_settings()
    today = today or clock.today()
    with session_factory() as db:
        report = watcher.run_once(db, fetch or watcher.PoliteFetcher(), today=today)
        published, held, deliveries = publish_trusted(db, report, today, settings)
    cycle = CycleReport(report, published, held)
    if deliveries:
        cycle.alerts = notifications.send_all(deliveries, session_factory, settings)
    watcher.notify_admin(report, settings, published=published, held=held)
    log.info("Watch cycle: %d published, %d held for review", len(published), len(held))
    return cycle


# --- Running inside the web app ------------------------------------------------


class BackgroundWatcher:
    """Runs a cycle now and then every `minutes`, on a daemon thread."""

    def __init__(self, minutes: float, first_delay: float = 20.0) -> None:
        self.minutes = minutes
        self.first_delay = first_delay
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="walang-pasok-watcher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(self.first_delay):  # let the server finish starting
            return
        while not self._stop.is_set():
            try:
                run_cycle()
            except Exception:  # noqa: BLE001 - one bad cycle must not stop the watcher
                log.exception("Watch cycle failed")
            self._stop.wait(self.minutes * 60)


def start_background(settings: Settings | None = None) -> BackgroundWatcher | None:
    settings = settings or get_settings()
    if not settings.watcher_enabled:
        return None
    worker = BackgroundWatcher(settings.watch_interval_minutes)
    worker.start()
    log.info("Background watcher on: every %s minutes", settings.watch_interval_minutes)
    return worker
