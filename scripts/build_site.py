"""Build the static website and JSON API for GitHub Pages.

    python -m scripts.build_site --out _site --base /walang-pasok-api --site-url https://you.github.io/walang-pasok-api

GitHub Pages only serves files, so this renders everything ahead of time with
the same templates and API code the live server uses:

    index.html                              homepage (today's nationwide list)
    p/<slug>/index.html                     one page per city/municipality
    api/index.html                          how to use the JSON files
    api/v1/check/<slug>.json                is there class today? (same shape as GET /api/v1/check)
    api/v1/check/<slug>/tomorrow.json       ...and tomorrow
    api/v1/suspensions/today.json           today's verified suspensions nationwide
    api/v1/suspensions.json                 the latest 100 verified suspensions
    api/v1/locations.json                   every place, with its slug and PSGC code
    api/v1/locations/grouped.json           the website's dropdown, by province

The database (Neon in production) is first copied into memory, so 1,600+ pages
render in seconds instead of making thousands of network round trips.
"""

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["WATCHER_ENABLED"] = "false"  # building must never start the news watcher

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_db, make_engine  # noqa: E402
from app.main import STATIC_DIR, create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.services import clock  # noqa: E402
from app.services import locations as location_service  # noqa: E402
from app.web import routes  # noqa: E402

TEMPLATES_DIR = Path(routes.__file__).resolve().parent.parent / "templates"
# Private or bulky tables the public site never shows.
SKIP_TABLES = {"subscriptions", "watched_pages"}


def snapshot(source_url: str) -> Session:
    """An in-memory copy of the public tables of the database at `source_url`."""
    source = make_engine(source_url)
    memory = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(memory)
    with source.connect() as src, memory.begin() as dst:
        for table in Base.metadata.sorted_tables:
            if table.name in SKIP_TABLES:
                continue
            rows = [dict(r) for r in src.execute(select(table)).mappings()]
            if rows:
                dst.execute(table.insert(), rows)
    source.dispose()
    return sessionmaker(bind=memory, autoflush=False, expire_on_commit=False)()


def write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def get(client: TestClient, url: str, **params) -> str:
    response = client.get(url, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"{url} {params}: HTTP {response.status_code}: {response.text[:200]}")
    return response.text


def build(out: Path, base: str, site_url: str, source_url: str) -> dict[str, int]:
    started = time.monotonic()
    db = snapshot(source_url)
    routes.templates.env.globals.update(static_site=True, site_base=base.rstrip("/"))
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    localities = location_service.list_localities(db)
    today = clock.today()
    counts = {"pages": 0, "json": 0}

    with TestClient(app) as client:
        home = get(client, "/")
        write(out / "index.html", home)
        write(out / "404.html", home)
        counts["pages"] += 1
        for loc in localities:
            write(out / "p" / loc.slug / "index.html", get(client, "/", q=loc.slug))
            counts["pages"] += 1

        api = out / "api" / "v1"
        for loc in localities:
            write(api / "check" / f"{loc.slug}.json", get(client, "/api/v1/check", location=loc.slug))
            write(api / "check" / loc.slug / "tomorrow.json", get(
                client, "/api/v1/check", location=loc.slug, date=(today + dt.timedelta(days=1)).isoformat()
            ))
            counts["json"] += 2
        write(api / "suspensions" / "today.json", get(client, "/api/v1/suspensions/today", size=100))
        write(api / "suspensions.json", get(client, "/api/v1/suspensions", size=100))

        places, page = [], 1
        while True:
            chunk = json.loads(get(client, "/api/v1/locations", size=100, page=page))
            places.extend(chunk["items"])
            if page >= chunk["pages"]:
                break
            page += 1
        write(api / "locations.json", json.dumps(places, ensure_ascii=False))
        grouped = [
            {"province": province, "places": [{"slug": p.slug, "name": p.name} for p in group]}
            for province, group in routes._group_by_province(localities)
        ]
        write(api / "locations" / "grouped.json", json.dumps(grouped, ensure_ascii=False))
        counts["json"] += 4

    docs = routes.templates.get_template("api_docs.html").render(
        updated=routes._time(clock.now()), site_url=site_url.rstrip("/"), today=today,
        example=next((loc.slug for loc in localities if loc.slug == "angeles-city"), localities[0].slug),
    )
    write(out / "api" / "index.html", docs)
    counts["pages"] += 1

    shutil.copytree(STATIC_DIR, out / "static")
    shutil.copy(TEMPLATES_DIR / "style.css", out / "static" / "style.css")
    shutil.copy(STATIC_DIR / "favicon.ico", out / "favicon.ico")
    write(out / ".nojekyll", "")  # serve files as they are, no Jekyll processing
    counts["seconds"] = round(time.monotonic() - started)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="_site", help="output folder (default: _site)")
    parser.add_argument("--base", default=os.environ.get("SITE_BASE", ""),
                        help='path the site is served under, e.g. "/walang-pasok-api" for a project page')
    parser.add_argument("--site-url", default=os.environ.get("SITE_URL", "http://localhost:8000"),
                        help="the site's public address, used in the API examples")
    parser.add_argument("--database-url", default=get_settings().database_url,
                        help="database to publish (default: DATABASE_URL)")
    args = parser.parse_args()
    if args.base and (not args.base.startswith("/") or ":" in args.base):
        # Git Bash on Windows turns "/repo" into "C:/Program Files/Git/repo"; use MSYS_NO_PATHCONV=1.
        parser.error(f'--base must be a URL path like "/walang-pasok-api", got {args.base!r}')
    counts = build(Path(args.out), args.base, args.site_url, args.database_url)
    print(f"Built {counts['pages']} pages and {counts['json']} JSON files into {args.out}/ in {counts['seconds']}s")


if __name__ == "__main__":
    sys.exit(main())
