"""Shared FastAPI dependencies: DB session, "today", pagination, admin auth."""

import datetime as dt
from dataclasses import dataclass
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Query, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, session_scope
from app.errors import UnauthorizedError
from app.services import clock
from app.services.notifications import SessionFactory

DbSession = Annotated[Session, Depends(get_db)]


def get_today() -> dt.date:
    """Today in Asia/Manila. Overridden in tests to freeze time."""
    return clock.today()


Today = Annotated[dt.date, Depends(get_today)]


def get_session_factory() -> SessionFactory:
    """How background tasks open a session. Overridden in tests."""
    return session_scope


SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]


@dataclass(frozen=True)
class Pagination:
    page: int
    size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def get_pagination(
    page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
    size: Annotated[
        int, Query(ge=1, le=get_settings().max_page_size, description="Items per page.")
    ] = get_settings().default_page_size,
) -> Pagination:
    return Pagination(page=page, size=size)


PageParams = Annotated[Pagination, Depends(get_pagination)]


_api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="Admin API key (see ADMIN_API_KEY)."
)


def require_admin(
    api_key: Annotated[str | None, Security(_api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if not api_key or not compare_digest(api_key.encode(), settings.admin_api_key.encode()):
        raise UnauthorizedError("Missing or invalid API key. Send it in the X-API-Key header.")
