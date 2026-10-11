import datetime as dt

from app.models import Level, Mode, SchoolType, Suspension, TimeScope
from app.web import timetable

MONDAY = dt.date(2026, 10, 12)
SATURDAY = dt.date(2026, 10, 10)


def sus(*levels, **fields) -> Suspension:
    s = Suspension(
        mode=fields.pop("mode", Mode.ALL_CLASSES),
        time_scope=fields.pop("time_scope", TimeScope.WHOLE_DAY),
        school_type=fields.pop("school_type", SchoolType.BOTH),
        **fields,
    )
    s.levels = list(levels or (Level.ALL,))
    return s


def test_school_days_skip_sunday():
    days = timetable.school_days(SATURDAY)

    assert len(days) == 6
    assert all(d.weekday() != 6 for d in days)


def test_regular_week_has_no_suspensions():
    week = timetable.build(MONDAY, lambda d: [])

    assert [d.tag for d in week.days[:2]] == ["Ngayon", "Bukas"]
    assert {c.label for row in week.rows for c in row.cells} == {"Regular", "No school"}
    # Saturday: no school for K-12, regular for college.
    saturday = [d.date.weekday() for d in week.days].index(5)
    assert week.rows[0].cells[saturday].label == "No school"
    assert week.rows[2].cells[saturday].label == "Regular"


def test_cells_reflect_scope():
    s_all = sus()
    s_f2f = sus(Level.COLLEGE, mode=Mode.FACE_TO_FACE_ONLY)
    s_pm = sus(Level.JUNIOR_HIGH, Level.SENIOR_HIGH, time_scope=TimeScope.AFTERNOON)
    per_day = {MONDAY: [s_all], MONDAY + dt.timedelta(days=1): [s_f2f, s_pm]}

    week = timetable.build(MONDAY, lambda d: per_day.get(d, []))
    k6, hs, college = (row.cells for row in week.rows)

    assert (k6[0].label, k6[0].sub, k6[0].state) == ("No class", "Suspended", "off")
    assert (college[1].label, college[1].sub) == ("Online", "F2F suspended")
    assert (hs[1].label, hs[1].sub) == ("Half day", "Afternoon off")
    assert k6[1].label == "Regular"


def test_partial_group_and_public_only():
    per_day = {
        MONDAY: [sus(Level.PRESCHOOL)],
        MONDAY + dt.timedelta(days=1): [sus(Level.COLLEGE, school_type=SchoolType.PUBLIC)],
    }

    week = timetable.build(MONDAY, lambda d: per_day.get(d, []))

    assert week.rows[0].cells[0].sub == "Some grades"
    assert week.rows[2].cells[1].sub == "Public schools"


def test_whole_day_beats_half_day():
    per_day = {MONDAY: [sus(time_scope=TimeScope.AFTERNOON), sus()]}

    week = timetable.build(MONDAY, lambda d: per_day.get(d, []))

    assert week.rows[0].cells[0].label == "No class"


def test_sunday_shown_only_when_suspended():
    sunday = SATURDAY + dt.timedelta(days=1)

    plain = timetable.build(SATURDAY, lambda d: [])
    with_sunday = timetable.build(SATURDAY, lambda d: [sus()] if d == sunday else [])

    assert sunday not in [d.date for d in plain.days]
    assert [d.date for d in with_sunday.days][:2] == [SATURDAY, sunday]
    assert with_sunday.days[1].tag == "Bukas"
    assert len(with_sunday.days) == 6
    assert with_sunday.rows[0].cells[1].label == "No class"
