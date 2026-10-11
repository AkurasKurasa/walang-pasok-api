"""Server-rendered website. No JavaScript: plain HTML forms and <details> keep it
fast on slow mobile connections and usable on any browser."""

import datetime as dt
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.deps import DbSession, SessionFactoryDep, Today
from app.config import get_settings
from app.errors import AppError
from app.models import Location, SchoolType, SourceKind, Suspension
from app.schemas import DiscordAlertCreate
from app.services import clock, notifications
from app.services import locations as location_service
from app.services import suspensions as suspension_service
from app.services.suspensions import SuspensionFilter
from app.web import timetable

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

LABELS = {
    "all": "All levels",
    "preschool": "Preschool",
    "elementary": "Elementary",
    "junior_high": "Junior high",
    "senior_high": "Senior high",
    "college": "College",
    "public": "Public schools",
    "private": "Private schools",
    "both": "Public & private",
    "all_classes": "All classes",
    "face_to_face_only": "Face-to-face only (online may continue)",
    "whole_day": "Whole day",
    "morning": "Morning only",
    "afternoon": "Afternoon only",
    "weather": "Weather",
    "holiday": "Holiday",
    "event": "Local event",
    "other": "Other",
    "facebook_post": "Facebook post",
    "x_post": "X post",
    "news_article": "News article",
    "official_website": "Official website",
}
LINK_TEXT = {
    SourceKind.FACEBOOK_POST: "Open original post",
    SourceKind.X_POST: "Open original post",
    SourceKind.NEWS_ARTICLE: "Read article",
    SourceKind.OFFICIAL_WEBSITE: "Open announcement",
    SourceKind.OTHER: "Open source",
}


def _manila(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:  # SQLite drops the offset; values are stored in UTC.
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(ZoneInfo(get_settings().timezone))


def _time(value: dt.datetime) -> str:
    local = _manila(value)
    return f"{local:%b} {local.day}, {local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def _initials(name: str) -> str:
    words = [w for w in name.replace("(", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


templates.env.filters["label"] = lambda value: LABELS.get(str(value), str(value).replace("_", " "))
templates.env.filters["longdate"] = lambda d: f"{d:%A}, {d:%B} {d.day}"
templates.env.filters["shortdate"] = lambda d: f"{d:%b} {d.day}"
templates.env.filters["dayname"] = lambda d: f"{d:%a} {d.day}"
templates.env.filters["posted"] = _time
templates.env.filters["initials"] = _initials
templates.env.globals["link_text"] = lambda kind: LINK_TEXT.get(kind, "Open source")

NATIONWIDE_LIMIT = 50

# Island group from the first two digits of the PSGC code (the region).
ISLANDS = {
    "luzon": {"01", "02", "03", "04", "05", "13", "14", "17"},
    "visayas": {"06", "07", "08", "18"},
    "mindanao": {"09", "10", "11", "12", "15", "16", "19"},
}


def island_of(location: Location) -> str | None:
    prefix = location.psgc_code[:2]
    return next((name for name, codes in ISLANDS.items() if prefix in codes), None)


def _resolve(db, query: str) -> tuple[Location | None, list[Location]]:
    """Exact slug first, then a name search. Returns (match, choices-if-ambiguous)."""
    exact = location_service.get_by_slug(db, query)
    if exact is not None:
        return exact, []
    found, _ = location_service.search(db, query, offset=0, limit=10)
    if len(found) == 1:
        return found[0], []
    return None, found


def _group_by_province(localities: list[Location]) -> list[tuple[str, list[Location]]]:
    """Dropdown groups: by province, with NCR cities (no province) under "Metro Manila"."""
    groups: dict[str, list[Location]] = {}
    for loc in localities:
        groups.setdefault(loc.province or "Metro Manila", []).append(loc)
    return sorted(groups.items())


def _base_context(db) -> dict:
    settings = get_settings()
    localities = location_service.list_localities(db)
    return {
        "updated": _time(clock.now()),
        "localities": localities,
        "locality_groups": _group_by_province(localities),
        "facebook_page_url": settings.facebook_page_url,
    }


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: DbSession,
    today: Today,
    q: Annotated[str | None, Query(max_length=100)] = None,
    island: Annotated[str | None, Query(max_length=20)] = None,
    on_date: Annotated[dt.date | None, Query(alias="date")] = None,
):
    # ?date= shows the page as it was (or will be) on another day.
    real_today = today
    today = on_date or today
    tomorrow = today + dt.timedelta(days=1)
    query = (q or "").strip()
    island = island if island in ISLANDS else None

    location, choices, days, week = None, [], [], None
    if query:
        location, choices = _resolve(db, query)
    if location is not None:
        # "Bukas" (tomorrow) is the question people ask, so it comes first.
        days = [
            ("Bukas", tomorrow, suspension_service.check(db, location, tomorrow)),
            ("Ngayon", today, suspension_service.check(db, location, today)),
        ]
        week = timetable.build(
            today, lambda d: suspension_service.check(db, location, d).suspensions
        )

    # The admit slip explains the most relevant day: tomorrow if suspended, else today.
    featured: list[Suspension] = []
    for _, _, result in days:
        if result.suspended:
            featured = result.suspensions
            break

    nationwide, nationwide_total = suspension_service.list_suspensions(
        db, SuspensionFilter(on_date=today), limit=NATIONWIDE_LIMIT
    )
    if island:
        nationwide = [s for s in nationwide if island_of(s.location) == island]

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            **_base_context(db),
            "query": query,
            "location": location,
            "choices": choices,
            "days": days,
            "featured": featured,
            "week": week,
            "today": today,
            "nationwide": nationwide,
            "nationwide_total": nationwide_total,
            "island": island,
            "archive": on_date is not None and on_date != real_today,
            "real_today": real_today,
            "recent_days": suspension_service.recent_days(db, before=real_today),
            "SchoolType": SchoolType,
        },
    )


@router.post("/alerts", response_class=HTMLResponse)
async def signup_alert(
    request: Request,
    db: DbSession,
    background: BackgroundTasks,
    session_factory: SessionFactoryDep,
):
    """The website's "Get alerts" form. Parsed by hand so no extra form library is needed."""
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
    webhook = form.get("webhook_url", "")
    place_text = form.get("location", "").strip()

    error, sub, location = None, None, None
    if place_text:
        location, choices = _resolve(db, place_text)
        if location is None:
            error = (
                f'Several places match "{place_text}". Type the full name, like "Angeles City".'
                if choices else f'We couldn\'t find "{place_text}". Leave it blank for nationwide alerts.'
            )
    if error is None:
        try:
            data = DiscordAlertCreate(
                webhook_url=webhook,
                location=location.slug if location else None,
                label=form.get("label") or None,
            )
            sub = notifications.create_discord_alert(db, data)
        except ValidationError as exc:
            error = exc.errors()[0]["msg"].removeprefix("Value error, ")
        except AppError as exc:
            error = exc.message

    if sub is not None:
        background.add_task(notifications.send_all, [notifications.welcome_delivery(sub)], session_factory)

    return templates.TemplateResponse(
        request,
        "alerts_result.html",
        {**_base_context(db), "error": error, "sub": sub},
        status_code=400 if error else 201,
    )


@router.post("/alerts/off", response_class=HTMLResponse)
async def turn_off_alert(request: Request, db: DbSession):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
    error = None
    try:
        notifications.deactivate_alert(db, form.get("token", "").strip())
    except AppError as exc:
        error = exc.message
    return templates.TemplateResponse(
        request,
        "alerts_result.html",
        {**_base_context(db), "error": error, "sub": None, "turned_off": error is None},
        status_code=404 if error else 200,
    )
