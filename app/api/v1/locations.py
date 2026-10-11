from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, PageParams
from app.schemas import LocationOut, Page
from app.services import locations as location_service

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("", response_model=Page[LocationOut], summary="Search locations")
def search_locations(
    db: DbSession,
    pagination: PageParams,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Matches name, slug or province (case-insensitive)."),
    ] = None,
):
    items, total = location_service.search(db, search, pagination.offset, pagination.size)
    return Page[LocationOut].build(
        [LocationOut.model_validate(loc) for loc in items], total, pagination.page, pagination.size
    )
