import datetime as dt
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.api.deps import DbSession, PageParams, SessionFactoryDep, Today, require_admin
from app.errors import BadRequestError
from app.models import Level, SchoolType
from app.schemas import ErrorResponse, Page, SourceCreate, SuspensionCreate, SuspensionOut
from app.services import locations as location_service
from app.services import notifications
from app.services import suspensions as suspension_service
from app.services.suspensions import SuspensionFilter

router = APIRouter(prefix="/suspensions", tags=["suspensions"])

LevelQuery = Annotated[
    Level | None,
    Query(description="Matches suspensions for this level or for `all` levels."),
]
SchoolTypeQuery = Annotated[
    SchoolType | None,
    Query(description="Matches suspensions for this school type or for `both`."),
]
_ADMIN_ERRORS = {401: {"model": ErrorResponse, "description": "Missing or invalid API key"}}


def _page(items, total: int, params) -> Page[SuspensionOut]:
    return Page[SuspensionOut].build(
        [SuspensionOut.model_validate(s) for s in items], total, params.page, params.size
    )


@router.get(
    "",
    response_model=Page[SuspensionOut],
    summary="List verified suspensions",
    responses={400: {"model": ErrorResponse, "description": "Unknown location slug"}},
)
def list_suspensions(
    db: DbSession,
    pagination: PageParams,
    on_date: Annotated[
        dt.date | None,
        Query(alias="date", description="Day to check (YYYY-MM-DD). Includes ongoing 'until lifted' suspensions."),
    ] = None,
    location: Annotated[
        str | None,
        Query(description="Location slug. Also includes suspensions declared for its province/region."),
    ] = None,
    level: LevelQuery = None,
    school_type: SchoolTypeQuery = None,
):
    location_ids = None
    if location is not None:
        loc = location_service.get_by_slug(db, location)
        if loc is None:
            raise BadRequestError(f"Unknown location '{location}'.", {"param": "location"})
        location_ids = location_service.coverage_ids(loc)

    f = SuspensionFilter(
        on_date=on_date, location_ids=location_ids, level=level, school_type=school_type
    )
    items, total = suspension_service.list_suspensions(db, f, pagination.offset, pagination.size)
    return _page(items, total, pagination)


@router.get(
    "/today",
    response_model=Page[SuspensionOut],
    summary="Today's verified suspensions nationwide (Asia/Manila)",
)
def list_today(
    db: DbSession,
    today: Today,
    pagination: PageParams,
    level: LevelQuery = None,
    school_type: SchoolTypeQuery = None,
):
    f = SuspensionFilter(on_date=today, level=level, school_type=school_type)
    items, total = suspension_service.list_suspensions(db, f, pagination.offset, pagination.size)
    return _page(items, total, pagination)


@router.get(
    "/{suspension_id}",
    response_model=SuspensionOut,
    summary="Get one verified suspension",
    responses={404: {"model": ErrorResponse}},
)
def get_suspension(suspension_id: int, db: DbSession):
    return suspension_service.get_public(db, suspension_id)


@router.post(
    "",
    response_model=SuspensionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Report a suspension (admin, starts as pending)",
    dependencies=[Depends(require_admin)],
    responses={**_ADMIN_ERRORS, 400: {"model": ErrorResponse, "description": "Unknown location"}},
)
def create_suspension(payload: SuspensionCreate, db: DbSession):
    return suspension_service.create(db, payload)


@router.post(
    "/{suspension_id}/sources",
    response_model=SuspensionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an extra source as proof (admin)",
    description="Attach another post or article that confirms the suspension, e.g. a news report.",
    dependencies=[Depends(require_admin)],
    responses={**_ADMIN_ERRORS, 404: {"model": ErrorResponse}},
)
def add_source(suspension_id: int, payload: SourceCreate, db: DbSession):
    return suspension_service.add_source(db, suspension_id, payload)


@router.patch(
    "/{suspension_id}/verify",
    response_model=SuspensionOut,
    summary="Mark a suspension verified (admin)",
    description=(
        "Makes the suspension public. The first time, it also sends Discord alerts to "
        "channels following that place and posts to the Facebook Page if one is configured. "
        "Alerts are sent after the response, so this stays fast."
    ),
    dependencies=[Depends(require_admin)],
    responses={**_ADMIN_ERRORS, 404: {"model": ErrorResponse}},
)
def verify_suspension(
    suspension_id: int,
    db: DbSession,
    today: Today,
    background: BackgroundTasks,
    session_factory: SessionFactoryDep,
):
    suspension, newly_verified = suspension_service.verify_with_change(db, suspension_id)
    if newly_verified:
        deliveries = notifications.plan(db, suspension, today)
        if deliveries:
            background.add_task(notifications.send_all, deliveries, session_factory)
    return suspension
