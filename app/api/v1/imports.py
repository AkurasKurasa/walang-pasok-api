from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_admin
from app.schemas import ErrorResponse, LocationOut
from app.schemas.imports import ImportedItem, ImportRequest, ImportResult
from app.services import importer

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post(
    "",
    response_model=ImportResult,
    summary="Read suspensions from announcement text (admin)",
    description=(
        "Paste the text of a news article or LGU post. Each line like "
        "`Angeles City - all levels, public and private` becomes a suspension, with the line "
        "saved as its proof. With `dry_run` (the default) nothing is saved, so you can check "
        "first. Otherwise they're saved as **pending**; verify them to make them public. "
        "If a place already has a suspension that day, the article is added to its proof instead."
    ),
    dependencies=[Depends(require_admin)],
    responses={401: {"model": ErrorResponse}},
)
def import_text(payload: ImportRequest, db: DbSession) -> ImportResult:
    index = importer.PlaceIndex.from_db(db)
    result = importer.parse_text(index, payload.text, payload.date, payload.reason)
    if not payload.dry_run:
        importer.import_parsed(
            db,
            result,
            payload.date,
            importer.SourceInfo(
                url=str(payload.source_url),
                name=payload.source_name,
                kind=payload.source_kind,
                posted_at=payload.source_posted_at,
                reason_detail=payload.reason_detail,
            ),
        )
    return ImportResult(
        date=payload.date,
        dry_run=payload.dry_run,
        items=[
            ImportedItem(
                line=c.line,
                location=LocationOut.model_validate(c.location),
                levels=c.levels,
                school_type=c.school_type,
                mode=c.mode,
                time_scope=c.time_scope,
                until_lifted=c.until_lifted,
                lifted_on=c.lifted_on,
                reason=c.reason,
                notes=c.notes,
                action=c.action,
                suspension_id=c.suspension_id,
            )
            for c in result.candidates
        ],
        unmatched=result.unmatched,
    )
