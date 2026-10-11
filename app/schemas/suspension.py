import datetime as dt

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import Level, Mode, Reason, SchoolType, SourceKind, Status, TimeScope
from app.schemas.location import LocationOut


class SourceOut(BaseModel):
    """One piece of proof: the post or article that announced the suspension."""

    model_config = ConfigDict(from_attributes=True)

    kind: SourceKind
    name: str = Field(examples=["Angeles City Information Office"])
    url: str
    excerpt: str | None = Field(description="The announcement text, quoted as posted.")
    posted_at: dt.datetime | None
    primary: bool = Field(description="True for the official source the suspension is based on.")


class SourceCreate(BaseModel):
    """An extra source that backs up a suspension (e.g. a news article)."""

    url: AnyHttpUrl = Field(examples=["https://newsinfo.example.ph/walang-pasok-oct-11"])
    name: str = Field(min_length=1, max_length=120, examples=["Example News"])
    kind: SourceKind | None = Field(default=None, description="Guessed from the URL if omitted.")
    excerpt: str | None = Field(default=None, max_length=5000)
    posted_at: dt.datetime | None = None


class SuspensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    location: LocationOut
    date: dt.date
    levels: list[Level]
    school_type: SchoolType
    mode: Mode
    time_scope: TimeScope
    until_lifted: bool
    lifted_on: dt.date | None
    reason: Reason
    reason_detail: str | None
    source_url: str
    source_name: str
    proof: list[SourceOut] = Field(description="All sources, the primary (official) one first.")
    status: Status
    verified_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class SuspensionCreate(BaseModel):
    """Payload for reporting a new suspension. It is stored as `pending`."""

    location: str = Field(description="Location slug.", examples=["angeles-city"])
    date: dt.date = Field(description="First affected day (Asia/Manila).")
    levels: list[Level] = Field(min_length=1, examples=[["all"]])
    school_type: SchoolType = SchoolType.BOTH
    mode: Mode = Mode.ALL_CLASSES
    time_scope: TimeScope = TimeScope.WHOLE_DAY
    until_lifted: bool = False
    reason: Reason
    reason_detail: str | None = Field(
        default=None, max_length=1000, examples=["Tropical Storm Kristine, Signal No. 2"]
    )
    source_url: AnyHttpUrl = Field(examples=["https://www.facebook.com/AngelesCityGov/posts/123"])
    source_name: str = Field(min_length=1, max_length=120, examples=["Angeles City Information Office"])
    source_kind: SourceKind | None = Field(default=None, description="Guessed from the URL if omitted.")
    source_excerpt: str | None = Field(
        default=None, max_length=5000, description="The announcement text, quoted exactly as posted."
    )
    source_posted_at: dt.datetime | None = Field(default=None, description="When the source was posted.")
    extra_sources: list[SourceCreate] = Field(default_factory=list, max_length=10)

    @field_validator("levels")
    @classmethod
    def normalize_levels(cls, levels: list[Level]) -> list[Level]:
        # "all" subsumes every specific level; otherwise drop duplicates, keep order.
        if Level.ALL in levels:
            return [Level.ALL]
        return list(dict.fromkeys(levels))

    @model_validator(mode="after")
    def until_lifted_needs_whole_day(self) -> "SuspensionCreate":
        if self.until_lifted and self.time_scope is not TimeScope.WHOLE_DAY:
            raise ValueError("An 'until lifted' suspension must cover the whole day.")
        return self


class CheckSource(BaseModel):
    name: str
    url: str


class CheckResult(BaseModel):
    """Answer to "are classes suspended here, on this day, for this level?"."""

    suspended: bool
    location: LocationOut
    date: dt.date
    level: Level | None
    school_type: SchoolType | None
    details: list[SuspensionOut]
    sources: list[CheckSource]
