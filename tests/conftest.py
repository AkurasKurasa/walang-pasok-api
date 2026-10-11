import datetime as dt
import os
from collections.abc import Callable, Iterator
from contextlib import nullcontext

# Configure before the app is imported: tests never touch a real database.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ADMIN_API_KEY"] = "test-admin-key"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_session_factory, get_today
from app.db import get_db
from app.main import create_app
from app.models import (
    Base,
    Level,
    Location,
    LocationType,
    Reason,
    Status,
    Suspension,
)

TODAY = dt.date(2026, 10, 12)  # a Monday
TOMORROW = TODAY + dt.timedelta(days=1)
YESTERDAY = TODAY - dt.timedelta(days=1)
ADMIN_HEADERS = {"X-API-Key": "test-admin-key"}


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_today] = lambda: TODAY
    # Background tasks reuse the test session instead of opening a real one.
    app.dependency_overrides[get_session_factory] = lambda: (lambda: nullcontext(db))
    with TestClient(app) as c:
        yield c


class Outbox:
    """Captures outgoing Discord/Facebook requests instead of sending them."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.next_status = 204

    def post(self, url, body, content_type, timeout):
        from app.services.notifications import HttpResult

        self.requests.append({"url": url, "body": body, "content_type": content_type})
        status = self.next_status
        ok = 200 <= status < 300
        return HttpResult(ok=ok, status=status, error=None if ok else f"HTTP {status}")

    def json_bodies(self) -> list[dict]:
        import json

        return [json.loads(r["body"]) for r in self.requests if r["content_type"] == "application/json"]


@pytest.fixture(autouse=True)
def outbox(monkeypatch) -> Outbox:
    """Autouse: no test can ever reach Discord or Facebook for real."""
    from app.services import notifications

    box = Outbox()
    monkeypatch.setattr(notifications, "post", box.post)
    return box


@pytest.fixture
def places(db: Session) -> dict[str, Location]:
    """A small hierarchy covering the interesting cases:

    central-luzon (region)
    ├── pampanga (province)
    │   └── san-fernando (component city)
    └── angeles-city (highly urbanized, independent of the province)
    metro-manila (region)
    └── quezon-city
    """
    def loc(code, name, slug, type_, region, province=None, parent=None) -> Location:
        location = Location(
            psgc_code=code, name=name, slug=slug, type=type_,
            region=region, province=province, parent=parent,
        )
        db.add(location)
        return location

    r3 = loc("0300000000", "Central Luzon", "central-luzon", LocationType.REGION, "Central Luzon")
    pampanga = loc("0305400000", "Pampanga", "pampanga", LocationType.PROVINCE, "Central Luzon", "Pampanga", r3)
    sf = loc("0305416000", "City of San Fernando", "san-fernando", LocationType.CITY, "Central Luzon", "Pampanga", pampanga)
    angeles = loc("0330100000", "Angeles City", "angeles-city", LocationType.CITY, "Central Luzon", "Pampanga", r3)
    ncr = loc("1300000000", "Metro Manila", "metro-manila", LocationType.REGION, "NCR")
    qc = loc("1381300000", "Quezon City", "quezon-city", LocationType.CITY, "NCR", None, ncr)
    db.commit()
    return {
        "central-luzon": r3, "pampanga": pampanga, "san-fernando": sf,
        "angeles-city": angeles, "metro-manila": ncr, "quezon-city": qc,
    }


MakeSuspension = Callable[..., Suspension]


@pytest.fixture
def make_suspension(db: Session) -> MakeSuspension:
    """Create a suspension with sensible defaults (verified, today, all levels)."""
    def make(location: Location, *, levels=(Level.ALL,), **fields) -> Suspension:
        values = {
            "date": TODAY,
            "reason": Reason.WEATHER,
            "source_url": f"https://example.org/{location.slug}",
            "source_name": f"{location.name} PIO",
            "status": Status.VERIFIED,
            **fields,
        }
        suspension = Suspension(location=location, **values)
        suspension.levels = list(levels)
        db.add(suspension)
        db.commit()
        return suspension

    return make
