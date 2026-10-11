from fastapi import APIRouter

from app.api.v1 import alerts, check, imports, locations, suspensions

router = APIRouter(prefix="/api/v1")
router.include_router(suspensions.router)
router.include_router(check.router)
router.include_router(locations.router)
router.include_router(alerts.router)
router.include_router(imports.router)
