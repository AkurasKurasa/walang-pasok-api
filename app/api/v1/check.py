import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Today
from app.errors import NotFoundError
from app.models import Level, SchoolType
from app.schemas import CheckResult, CheckSource, ErrorResponse, LocationOut, SuspensionOut
from app.services import locations as location_service
from app.services import suspensions as suspension_service

router = APIRouter(tags=["check"])


@router.get(
    "/check",
    response_model=CheckResult,
    summary="Are classes suspended here?",
    description=(
        "Answers whether classes are suspended in a location on a day. "
        "Counts verified suspensions for the location itself and for its province/region, "
        "including ongoing 'until lifted' suspensions. "
        "`suspended` is true even for partial suspensions (e.g. afternoon only); "
        "read `details` for the scope."
    ),
    responses={404: {"model": ErrorResponse, "description": "Unknown location slug"}},
)
def check(
    db: DbSession,
    today: Today,
    location: Annotated[str, Query(description="Location slug, e.g. `angeles-city`.")],
    level: Annotated[Level | None, Query(description="Education level; omit for any.")] = None,
    school_type: Annotated[SchoolType | None, Query(description="Omit for any.")] = None,
    on_date: Annotated[
        dt.date | None, Query(alias="date", description="YYYY-MM-DD; defaults to today (Asia/Manila).")
    ] = None,
) -> CheckResult:
    loc = location_service.get_by_slug(db, location)
    if loc is None:
        raise NotFoundError(f"Location '{location}' not found.", {"param": "location"})

    day = on_date or today
    outcome = suspension_service.check(db, loc, day, level, school_type)
    return CheckResult(
        suspended=outcome.suspended,
        location=LocationOut.model_validate(loc),
        date=day,
        level=level,
        school_type=school_type,
        details=[SuspensionOut.model_validate(s) for s in outcome.suspensions],
        sources=[CheckSource(name=name, url=url) for name, url in outcome.sources],
    )
