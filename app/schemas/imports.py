import datetime as dt

from pydantic import AnyHttpUrl, BaseModel, Field

from app.models import Level, Mode, Reason, SchoolType, SourceKind, TimeScope
from app.schemas.location import LocationOut


class ImportRequest(BaseModel):
    """Announcement text to turn into suspensions, e.g. pasted from a news article or LGU post."""

    text: str = Field(min_length=1, max_length=50_000, description="The announcement or article text.")
    date: dt.date = Field(description="The day classes are suspended.")
    source_url: AnyHttpUrl = Field(description="Where the text came from. Saved as proof.")
    source_name: str = Field(min_length=1, max_length=120, examples=["Inquirer.net"])
    source_kind: SourceKind | None = Field(default=None, description="Guessed from the URL if omitted.")
    source_posted_at: dt.datetime | None = None
    reason: Reason = Field(default=Reason.WEATHER, description="Used when a line doesn't say.")
    reason_detail: str | None = Field(default=None, max_length=1000, examples=["Southwest monsoon (habagat)"])
    dry_run: bool = Field(default=True, description="Only show what would be saved.")


class ImportedItem(BaseModel):
    line: str = Field(description="The line the suspension was read from.")
    location: LocationOut
    levels: list[Level]
    school_type: SchoolType
    mode: Mode
    time_scope: TimeScope
    until_lifted: bool
    lifted_on: dt.date | None
    reason: Reason
    notes: list[str] = Field(description="Things a person should double-check.")
    action: str | None = Field(
        description="`created` (new pending suspension), `added_source` (proof added to an existing one), "
        "`already_listed`, or `null` on a dry run."
    )
    suspension_id: int | None


class ImportResult(BaseModel):
    date: dt.date
    dry_run: bool
    items: list[ImportedItem]
    unmatched: list[str] = Field(description="Lines that look like suspensions but name a place we don't know.")
