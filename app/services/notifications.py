"""Tell the outside world when a suspension is verified.

Two channels:

* **Discord webhooks.** Anyone can paste a channel webhook URL on the website
  (or POST /api/v1/alerts/discord) to get a message whenever a suspension is
  verified for the place they follow.
* **Your own Facebook Page.** If FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN
  are set, each verified suspension is also posted to that Page.

Building the messages needs the database, sending them doesn't. So the verify
endpoint builds the `Delivery` list while it still has its request session,
then hands it to `send_all` as a background task, which runs after the
response is sent and records results with a fresh session.
"""

import datetime as dt
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.config import Settings, get_settings
from app.errors import BadRequestError, ConflictError, NotFoundError
from app.models import (
    Channel,
    Level,
    Location,
    Mode,
    SchoolType,
    SourceKind,
    Subscription,
    Suspension,
    TimeScope,
)
from app.schemas.subscription import DiscordAlertCreate
from app.services import clock, locations

log = logging.getLogger(__name__)

USER_AGENT = f"WalangPasokBot/{__version__} (+https://github.com/AkurasKurasa)"
DISCORD_PINK = 0xE6007E
# Discord returns these when a webhook was deleted or its token is wrong:
# retrying will never work, so the subscription is switched off.
_DEAD_WEBHOOK_STATUSES = {401, 403, 404}
MAX_FAILURES = 5

LEVEL_LABELS = {
    Level.ALL: "All levels",
    Level.PRESCHOOL: "Preschool",
    Level.ELEMENTARY: "Elementary",
    Level.JUNIOR_HIGH: "Junior high",
    Level.SENIOR_HIGH: "Senior high",
    Level.COLLEGE: "College",
}
SCHOOL_LABELS = {
    SchoolType.BOTH: "Public & private",
    SchoolType.PUBLIC: "Public schools only",
    SchoolType.PRIVATE: "Private schools only",
}
SOURCE_LABELS = {
    SourceKind.FACEBOOK_POST: "Facebook post",
    SourceKind.X_POST: "X post",
    SourceKind.NEWS_ARTICLE: "News article",
    SourceKind.OFFICIAL_WEBSITE: "Official website",
    SourceKind.OTHER: "Source",
}


# --- Sending ---------------------------------------------------------------


@dataclass
class HttpResult:
    ok: bool
    status: int | None
    error: str | None = None


def post(url: str, body: bytes, content_type: str, timeout: float) -> HttpResult:
    """POST and report the outcome. Never raises: one failing channel must not
    stop the others. Tests replace this function."""
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": content_type, "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResult(ok=200 <= response.status < 300, status=response.status)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        return HttpResult(ok=False, status=exc.code, error=f"HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return HttpResult(ok=False, status=None, error=str(exc))


def post_json(url: str, payload: dict, timeout: float) -> HttpResult:
    return post(url, json.dumps(payload).encode(), "application/json", timeout)


def post_form(url: str, fields: dict[str, str], timeout: float) -> HttpResult:
    return post(url, urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded", timeout)


# --- Who gets told ---------------------------------------------------------


def _ancestor_ids(location: Location) -> set[int]:
    return set(locations.coverage_ids(location))


def matches(subscription: Subscription, suspension: Suspension) -> bool:
    """Does `subscription` care about `suspension`?

    Yes if it is nationwide, if the suspension was declared for the followed
    place or a wider area covering it (a region-wide suspension reaches every
    city's followers), or if the suspension is inside the followed place
    (followers of Pampanga hear about San Fernando).
    """
    followed = subscription.location
    if followed is None:
        return True
    declared = suspension.location
    return declared.id in _ancestor_ids(followed) or followed.id in _ancestor_ids(declared)


def is_current(suspension: Suspension, today: dt.date) -> bool:
    """Only announce suspensions for today or later."""
    if suspension.date >= today:
        return True
    return suspension.until_lifted and (suspension.lifted_on is None or suspension.lifted_on > today)


# --- Messages --------------------------------------------------------------


def site_link(suspension: Suspension, settings: Settings) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/?q={urllib.parse.quote(suspension.location.slug)}"


def _scope_lines(s: Suspension) -> list[str]:
    lines = [", ".join(LEVEL_LABELS[level] for level in s.levels), SCHOOL_LABELS[s.school_type]]
    if s.mode is Mode.FACE_TO_FACE_ONLY:
        lines.append("Face-to-face only (online classes may continue)")
    if s.time_scope is TimeScope.MORNING:
        lines.append("Morning only")
    elif s.time_scope is TimeScope.AFTERNOON:
        lines.append("Afternoon only")
    if s.until_lifted:
        lines.append("Until lifted")
    return lines


def _day(s: Suspension) -> str:
    return f"{s.date:%A}, {s.date:%B} {s.date.day}"


def headline(s: Suspension) -> str:
    return f"Walang pasok: {s.location.name}, {_day(s)}"


def discord_payload(s: Suspension, settings: Settings) -> dict:
    proof = "\n".join(
        f"[{SOURCE_LABELS[p.kind]}: {p.name}]({p.url})" for p in s.proof
    )
    fields = [
        {"name": "Who", "value": "\n".join(_scope_lines(s)), "inline": True},
        {"name": "Why", "value": s.reason_detail or s.reason.value.title(), "inline": True},
        {"name": "Proof", "value": proof[:1024], "inline": False},
    ]
    return {
        "username": "Walang Pasok",
        "allowed_mentions": {"parse": []},  # never ping @everyone, whatever the text says
        "embeds": [
            {
                "title": headline(s)[:256],
                "url": site_link(s, settings),
                "color": DISCORD_PINK,
                "fields": fields,
                "footer": {"text": "Verified against the official announcement. Always confirm with your school."},
            }
        ],
    }


def facebook_message(s: Suspension) -> str:
    lines = [f"WALANG PASOK: {s.location.name}", _day(s), ""]
    lines += [f"• {line}" for line in _scope_lines(s)]
    if s.reason_detail:
        lines += ["", s.reason_detail]
    lines += ["", f"Source: {s.source_name}", s.source_url]
    return "\n".join(lines)


# --- Deliveries ------------------------------------------------------------


@dataclass
class Delivery:
    """One message to send. `subscription_id` is None for the Facebook Page post."""

    url: str
    payload: dict
    form: bool = False
    subscription_id: int | None = None
    channel_label: str = "discord"


@dataclass
class DispatchReport:
    sent: int = 0
    failed: int = 0
    deactivated: list[int] = field(default_factory=list)


def plan(
    db: Session, suspension: Suspension, today: dt.date, settings: Settings | None = None
) -> list[Delivery]:
    """Everything to send now that `suspension` has been verified."""
    settings = settings or get_settings()
    if not is_current(suspension, today):
        return []

    deliveries: list[Delivery] = []
    subscriptions = db.scalars(
        select(Subscription).where(
            Subscription.active.is_(True), Subscription.channel == Channel.DISCORD_WEBHOOK
        )
    ).all()
    payload = None
    for sub in subscriptions:
        if matches(sub, suspension):
            payload = payload or discord_payload(suspension, settings)
            deliveries.append(Delivery(url=sub.target, payload=payload, subscription_id=sub.id))

    if settings.facebook_enabled:
        url = (
            f"https://graph.facebook.com/{settings.facebook_graph_version}/"
            f"{settings.facebook_page_id}/feed"
        )
        deliveries.append(
            Delivery(
                url=url,
                payload={
                    "message": facebook_message(suspension),
                    "link": site_link(suspension, settings),
                    "access_token": settings.facebook_page_access_token,
                },
                form=True,
                channel_label="facebook",
            )
        )
    return deliveries


SessionFactory = Callable[[], AbstractContextManager[Session]]


def send_all(
    deliveries: Iterable[Delivery],
    session_factory: SessionFactory,
    settings: Settings | None = None,
) -> DispatchReport:
    """Send every delivery and record the result on its subscription."""
    settings = settings or get_settings()
    report = DispatchReport()
    results: list[tuple[Delivery, HttpResult]] = []
    for d in deliveries:
        if d.form:
            result = post_form(d.url, d.payload, settings.notify_timeout)
        else:
            result = post_json(d.url, d.payload, settings.notify_timeout)
        results.append((d, result))
        if result.ok:
            report.sent += 1
        else:
            report.failed += 1
            # Never log the URL: Discord webhook URLs and FB tokens are secrets.
            log.warning("%s delivery failed: %s", d.channel_label, result.error)

    tracked = [(d, r) for d, r in results if d.subscription_id is not None]
    if not tracked:
        return report
    with session_factory() as db:
        for d, r in tracked:
            sub = db.get(Subscription, d.subscription_id)
            if sub is None:
                continue
            if r.ok:
                sub.failure_count, sub.last_error, sub.last_sent_at = 0, None, clock.now()
            else:
                sub.failure_count += 1
                sub.last_error = (r.error or "unknown error")[:1000]
                if r.status in _DEAD_WEBHOOK_STATUSES or sub.failure_count >= MAX_FAILURES:
                    sub.active = False
                    report.deactivated.append(sub.id)
        db.commit()
    return report


# --- Managing Discord alerts -----------------------------------------------


def create_discord_alert(db: Session, data: DiscordAlertCreate) -> Subscription:
    location = None
    if data.location:
        location = locations.get_by_slug(db, data.location)
        if location is None:
            raise BadRequestError(f"Unknown location '{data.location}'.", {"field": "location"})

    same_place = (
        Subscription.location_id == location.id if location else Subscription.location_id.is_(None)
    )
    existing = db.scalar(
        select(Subscription).where(
            Subscription.channel == Channel.DISCORD_WEBHOOK,
            Subscription.target == data.webhook_url,
            same_place,
        )
    )
    if existing is not None and existing.active:
        raise ConflictError("That channel already gets alerts for this place.")

    sub = existing or Subscription(channel=Channel.DISCORD_WEBHOOK, target=data.webhook_url)
    sub.location = location
    sub.label = data.label
    sub.active = True
    sub.failure_count = 0
    sub.last_error = None
    # A fresh token every time, so an old leaked one stops working.
    sub.manage_token = secrets.token_urlsafe(24)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def deactivate_alert(db: Session, manage_token: str) -> None:
    sub = db.scalar(select(Subscription).where(Subscription.manage_token == manage_token))
    if sub is None or not sub.active:
        raise NotFoundError("No active alert with that token.")
    sub.active = False
    db.commit()


def welcome_delivery(sub: Subscription) -> Delivery:
    where = sub.location.name if sub.location else "the whole Philippines"
    payload = {
        "username": "Walang Pasok",
        "allowed_mentions": {"parse": []},
        "content": (
            f"Ring ring! This channel will now get class suspension alerts for **{where}** "
            "as soon as they're verified against the official announcement."
        ),
    }
    return Delivery(url=sub.target, payload=payload, subscription_id=sub.id)
