from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

from app import __version__
from app.api.v1 import router as api_v1_router
from app.errors import register_error_handlers
from app.web.routes import router as web_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

DESCRIPTION = """
Public API for **class suspensions in the Philippines** ("walang pasok") due to
typhoons, floods, holidays and local events.

* Public endpoints only return **verified** suspensions.
* "Today" is always computed in **Asia/Manila**.
* Locations are identified by URL-friendly slugs (e.g. `angeles-city`) backed by PSGC codes.
* Every suspension links to its **proof**: the official post, plus any news reports.
* Admin endpoints need an `X-API-Key` header.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Walang Pasok API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=[
            {"name": "check", "description": "The quick yes/no answer."},
            {"name": "suspensions", "description": "Browse and manage suspensions."},
            {"name": "locations", "description": "Find location slugs."},
            {"name": "alerts", "description": "Get Discord messages when suspensions are verified."},
            {"name": "imports", "description": "Read suspensions from article or announcement text (admin)."},
        ],
    )
    app.add_middleware(GZipMiddleware, minimum_size=500)
    register_error_handlers(app)
    app.include_router(api_v1_router)
    app.include_router(web_router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Browsers ask for /favicon.ico on their own, even without a <link> tag.
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
