"""The weekly timetable on the website: for one place, which school levels
have class on each of the next six school days."""

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.models import Level, Mode, SchoolType, Suspension, TimeScope

ROWS: list[tuple[str, list[Level]]] = [
    ("Preschool–Gr 6", [Level.PRESCHOOL, Level.ELEMENTARY]),
    ("JHS · SHS", [Level.JUNIOR_HIGH, Level.SENIOR_HIGH]),
    ("College", [Level.COLLEGE]),
]
DAYS_SHOWN = 6

# Higher is worse. A day with both an afternoon-only and a whole-day
# suspension shows the whole-day one.
_OFF, _PARTIAL, _CLASS, _REST = "off", "partial", "class", "rest"
_SEVERITY = {_OFF: 2, _PARTIAL: 1}


@dataclass(frozen=True)
class Cell:
    state: str  # "off", "partial", "class" or "rest"
    label: str  # big word: "No class", "Online", "Half day", "Regular", "No school"
    sub: str  # small caps line above it


@dataclass(frozen=True)
class Day:
    date: dt.date
    tag: str | None  # "Ngayon", "Bukas" or None


@dataclass(frozen=True)
class Row:
    label: str
    cells: list[Cell]


@dataclass(frozen=True)
class Timetable:
    days: list[Day]
    rows: list[Row]


def school_days(start: dt.date, count: int = DAYS_SHOWN) -> list[dt.date]:
    """`count` days from `start`, skipping Sundays."""
    days, day = [], start
    while len(days) < count:
        if day.weekday() != 6:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def effect(s: Suspension) -> tuple[str, str, str]:
    if s.mode is Mode.FACE_TO_FACE_ONLY:
        return _PARTIAL, "Online", "F2F suspended"
    if s.time_scope is TimeScope.AFTERNOON:
        return _PARTIAL, "Half day", "Afternoon off"
    if s.time_scope is TimeScope.MORNING:
        return _PARTIAL, "Half day", "Morning off"
    return _OFF, "No class", "Suspended"


def _affects(s: Suspension, level: Level) -> bool:
    levels = s.levels
    return Level.ALL in levels or level in levels


def cell(suspensions: Sequence[Suspension], levels: list[Level], day: dt.date) -> Cell:
    hits: list[tuple[Suspension, tuple[str, str, str]]] = []
    affected_levels = 0
    for level in levels:
        mine = [(s, effect(s)) for s in suspensions if _affects(s, level)]
        if mine:
            affected_levels += 1
            hits.extend(mine)

    if not hits:
        if day.weekday() == 6 or (day.weekday() == 5 and Level.COLLEGE not in levels):
            return Cell(_REST, "No school", "Weekend")
        return Cell(_CLASS, "Regular", "Class")

    _, (state, label, sub) = max(hits, key=lambda h: _SEVERITY[h[1][0]])
    if all(s.school_type is SchoolType.PUBLIC for s, _ in hits):
        sub = "Public schools"
    elif all(s.school_type is SchoolType.PRIVATE for s, _ in hits):
        sub = "Private schools"
    elif affected_levels < len(levels):
        sub = "Some grades"
    return Cell(state, label, sub)


def build(
    start: dt.date,
    suspensions_on: Callable[[dt.date], Sequence[Suspension]],
) -> Timetable:
    """`suspensions_on(day)` returns the verified suspensions covering the place that day."""
    dates = school_days(start)
    # Sundays are skipped unless something was declared for one (rare, but it
    # happens when a suspension starts the day before a school week).
    sunday = next(
        (start + dt.timedelta(days=i) for i in range(7) if (start + dt.timedelta(days=i)).weekday() == 6),
        None,
    )
    per_day = {d: list(suspensions_on(d)) for d in dates}
    if sunday is not None and sunday < dates[-1]:
        on_sunday = list(suspensions_on(sunday))
        if on_sunday:
            per_day[sunday] = on_sunday
            dates = sorted([*dates, sunday])[:DAYS_SHOWN]
    tags = {start: "Ngayon", start + dt.timedelta(days=1): "Bukas"}
    days = [Day(d, tags.get(d)) for d in dates]
    rows = [Row(label, [cell(per_day[d], levels, d) for d in dates]) for label, levels in ROWS]
    return Timetable(days=days, rows=rows)
