"""Turn the text of a suspension announcement into structured suspensions.

News sites and LGUs post lists like these:

    Pampanga
    San Simon – no face-to-face classes; all levels; public and private; until Friday, Sept. 11
    Angeles City, Pampanga: kindergarten to senior high school, public schools
    Caloocan City - all levels, public and private (afternoon classes)

`parse_text` reads each line, finds the place (using province headings and
"Place, Province" to tell same-named towns apart), and reads the levels,
public/private, face-to-face only, morning/afternoon and "until ..." from the
rest. Lines it can't place are returned as `unmatched` so a person can check them.

`import_parsed` saves the results as **pending** suspensions. If the same
place and day already has a suspension, the article is added to its proof
instead of creating a duplicate. Nothing becomes public until someone verifies it.

This is deliberately rule-based, not AI: it's predictable, testable, free to
run every few minutes, and the announcements follow a small set of patterns.
"""

import datetime as dt
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Level,
    Location,
    LocationType,
    Mode,
    Reason,
    SchoolType,
    SourceKind,
    Suspension,
    SuspensionSource,
    TimeScope,
)
from app.models.source import infer_kind
from app.schemas.suspension import SuspensionCreate
from app.services import clock
from app.services import suspensions as suspension_service

# --- Text helpers ------------------------------------------------------------

_BLOCK_TAGS = {
    "p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "ul", "ol", "table", "blockquote", "header", "footer",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "form", "iframe", "template"}


_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _TextExtractor(HTMLParser):
    def __init__(self, container_id: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        # Only keep text inside the element with this id (a news article's body).
        self.container_id = container_id
        self._depth = 0 if container_id else None  # None: keep everything
        self.found_container = False
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._depth is not None:
            if self._depth == 0 and dict(attrs).get("id") == self.container_id:
                self._depth, self.found_container = 1, True
            elif self._depth > 0 and tag not in _VOID_TAGS:
                self._depth += 1
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag):
        if self._depth and tag not in _VOID_TAGS:
            self._depth -= 1
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._link_text).split())))
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._depth is None or self._depth > 0:
            self.parts.append(data)
        if self._href is not None:
            self._link_text.append(data)


@dataclass
class Page:
    title: str
    text: str
    links: list[tuple[str, str]]  # (href, link text)


def html_to_page(html: str, container_id: str | None = None) -> Page:
    """Visible text (one block per line), the <title>, and every link.

    With `container_id`, text comes only from that element, so sidebars and
    "read next" headlines are left out. If the page has no such element (a
    site redesign), all of the page's text is used instead.
    """
    extractor = _TextExtractor(container_id)
    extractor.feed(html)
    if container_id and not extractor.found_container:
        return html_to_page(html)
    raw = "".join(extractor.parts)
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    text = "\n".join(line for line in lines if line)
    return Page(title=" ".join(extractor.title.split()), text=text, links=extractor.links)


def normalize(text: str) -> str:
    """Lowercase, no accents (Parañaque -> paranaque), Sta./Sto. spelled out, no punctuation."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\bsta\.?\s", "santa ", text)
    text = re.sub(r"\bsto\.?\s", "santo ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _place_keys(name: str) -> set[str]:
    """Ways a place's name is written: "City of San Fernando" -> {"city of san fernando", "san fernando"}."""
    key = normalize(name)
    keys = {key}
    for pattern in (r"^city of (.+)$", r"^(.+) city$", r"^province of (.+)$", r"^municipality of (.+)$"):
        m = re.match(pattern, key)
        if m:
            keys.add(m.group(1))
    return keys


# Other names for regions that announcements use.
_REGION_ALIASES = {
    "metro-manila": {"ncr", "metro manila", "national capital region"},
    "central-luzon": {"central luzon", "region iii", "region 3"},
}


class PlaceIndex:
    """Finds a Location from how its name is written in an announcement."""

    def __init__(self, places: Iterable[Location]) -> None:
        self.by_key: dict[str, list[Location]] = {}
        for loc in places:
            keys = _place_keys(loc.name) | _REGION_ALIASES.get(loc.slug, set())
            for key in keys:
                self.by_key.setdefault(key, []).append(loc)

    @classmethod
    def from_db(cls, db: Session) -> "PlaceIndex":
        return cls(db.scalars(select(Location)).all())

    def lookup(self, text: str, province_hint: str | None = None) -> Location | None:
        key = normalize(text)
        found = self.by_key.get(key, [])
        if not found:
            # "Manila City" vs "City of Manila", "Paranaque" vs "Paranaque City", ...
            for variant in _place_keys(text):
                found = self.by_key.get(variant, [])
                if found:
                    break
        if len(found) > 1 and province_hint:
            hint = normalize(province_hint)
            narrowed = [loc for loc in found if loc.province and normalize(loc.province) == hint]
            found = narrowed or found
        if len(found) > 1:
            # Prefer the city/municipality over a same-named province (e.g. "Bataan" the town).
            local = [loc for loc in found if loc.type in (LocationType.CITY, LocationType.MUNICIPALITY)]
            found = local if len(local) == 1 else found
        return found[0] if len(found) == 1 else None

    def is_heading(self, text: str) -> Location | None:
        """A line that is just a province or region name, used as a section heading."""
        loc = self.lookup(text.rstrip(":"))
        return loc if loc is not None and loc.type in (LocationType.PROVINCE, LocationType.REGION) else None


# --- Reading the details -----------------------------------------------------

_ORDER = [Level.PRESCHOOL, Level.ELEMENTARY, Level.JUNIOR_HIGH, Level.SENIOR_HIGH, Level.COLLEGE]
_LEVEL_PATTERNS: list[tuple[str, list[Level]]] = [
    (r"pre-?\s?school|kinder(?:garten)?|day\s?care|nursery|pre-?k\b", [Level.PRESCHOOL]),
    (r"elementary|grade school|primary", [Level.ELEMENTARY]),
    (r"junior high|\bjhs\b", [Level.JUNIOR_HIGH]),
    (r"senior high|\bshs\b", [Level.SENIOR_HIGH]),
    (r"(?<!junior )(?<!senior )\bhigh school", [Level.JUNIOR_HIGH, Level.SENIOR_HIGH]),
    (r"college|tertiary|universit", [Level.COLLEGE]),
]
_ALL_LEVELS = re.compile(r"all (?:school )?levels|lahat ng antas|all grade levels|\ball schools\b")
_LEVEL_WORDS = re.compile(
    r"all (?:school )?levels|lahat ng antas|classes|pasok|school|kinder|elementary|high|college|preschool|daycare",
    re.I,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?",
    re.I,
)
_UNTIL_RE = re.compile(r"\buntil\b([^;()]*)|\bhanggang\b([^;()]*)", re.I)


def find_date(text: str, near: dt.date) -> dt.date | None:
    """First "Oct. 20" / "July 21, 2025" style date in `text`. A missing year is
    the one that puts the date closest to `near` (an article's publish date)."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    month = _MONTHS[m.group(1).lower()[:4] if m.group(1).lower().startswith("sept") else m.group(1).lower()[:3]]
    day = int(m.group(2))
    years = [int(m.group(3))] if m.group(3) else [near.year - 1, near.year, near.year + 1]
    options = []
    for year in years:
        try:
            options.append(dt.date(year, month, day))
        except ValueError:
            continue
    return min(options, key=lambda d: abs((d - near).days)) if options else None


def parse_levels(text: str) -> list[Level]:
    t = text.lower()
    if _ALL_LEVELS.search(t):
        return [Level.ALL]
    hits: list[tuple[int, list[Level]]] = []
    for pattern, levels in _LEVEL_PATTERNS:
        for m in re.finditer(pattern, t):
            hits.append((m.start(), levels))
    if not hits:
        return [Level.ALL]
    hits.sort()
    first, last = hits[0], hits[-1]
    between = t[first[0]:last[0]]
    if len(hits) >= 2 and re.search(r"\bto\b|hanggang|\bthrough\b|[–-]", between):
        lo = min(_ORDER.index(level) for level in first[1])
        hi = max(_ORDER.index(level) for level in last[1])
        return _ORDER[lo:hi + 1]
    found = {level for _, levels in hits for level in levels}
    return [level for level in _ORDER if level in found]


def parse_school_type(text: str) -> SchoolType:
    t = text.lower()
    public, private = "public" in t, "private" in t
    if public and not private:
        return SchoolType.PUBLIC
    if private and not public:
        return SchoolType.PRIVATE
    return SchoolType.BOTH


def parse_mode(text: str) -> Mode:
    t = text.lower()
    if re.search(r"face[- ]to[- ]face|\bf2f\b|in-?person|onsite|on-site", t):
        return Mode.FACE_TO_FACE_ONLY
    return Mode.ALL_CLASSES


def parse_time_scope(text: str) -> TimeScope:
    t = text.lower()
    if re.search(r"afternoon|\bpm (?:classes|session)|hapon", t):
        return TimeScope.AFTERNOON
    if re.search(r"(?:from|simula|starting|beginning|effective)\s+(?:at\s+)?(?:1[0-2]|[1-9])(?::\d\d)?\s*(?:p\.?m)", t):
        return TimeScope.AFTERNOON
    if re.search(r"morning|\bam (?:classes|session)|umaga", t):
        return TimeScope.MORNING
    return TimeScope.WHOLE_DAY


def article_mode(title: str, intro: str = "") -> Mode:
    """In-person only for the whole article if its headline or first lines say so."""
    head = f"{title}\n{intro}".lower()
    if re.search(r"(?:in-?person|face[- ]to[- ]face|onsite|on-site) class(?:es)? (?:are |were )?suspend", head):
        return Mode.FACE_TO_FACE_ONLY
    if re.search(r"suspend\w* (?:of )?(?:in-?person|face[- ]to[- ]face|onsite) class", head):
        return Mode.FACE_TO_FACE_ONLY
    return Mode.ALL_CLASSES


def parse_reason(text: str, default: Reason) -> Reason:
    t = text.lower()
    if re.search(r"holiday|non-working", t):
        return Reason.HOLIDAY
    if re.search(r"fiesta|teachers['’]? day|foundation|celebration|event|anniversary|festival|strike", t):
        return Reason.EVENT
    return default


# --- Lines -> candidates -----------------------------------------------------

_SPLIT_RE = re.compile(r"\s+[–—-]\s+|\s*:\s+|\s+[–—]|[–—]\s+")

# Words that mark a heading as a region or island group rather than a province.
_REGION_WORDS = re.compile(
    r"\bregion\b|\bncr\b|metro manila|luzon|visayas|mindanao|cordillera|valley|calabarzon|mimaropa|"
    r"bicol|caraga|barmm|bangsamoro|soccsksargen|zamboanga peninsula|ilocos region|davao region",
    re.I,
)
_ISLAND_GROUPS = {"luzon", "visayas", "mindanao", "nationwide", "philippines"}
# Page furniture that sits between headings and lists on news sites.
_NOISE = re.compile(
    r"advertisement|featured stories|read more|read next|related|subscribe|sign up|^newsinfo$|^×$|refresh",
    re.I,
)


@dataclass(frozen=True)
class Scope:
    """What a heading like "Pampanga" or "Region III Central Luzon" allows below it."""

    kind: str  # "province", "region" or "any"
    name: str  # normalized

    def allows(self, loc: Location) -> bool:
        if self.kind == "any":
            return True
        if self.kind == "province":
            if loc.province:
                return normalize(loc.province) == self.name or normalize(loc.name) == self.name
            return False  # e.g. NCR cities have no province
        region = normalize(loc.region)
        if self.name in region or region in self.name:
            return True
        group = _region_group(self.name)
        return group is not None and group == _region_group(region)


def _region_group(text: str) -> str | None:
    """Which known region a name refers to: "NCR", "Metro Manila" and
    "National Capital Region (NCR)" are all "metro-manila"."""
    for slug, names in _REGION_ALIASES.items():
        if any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
            return slug
    return None


def scope_for(heading: str) -> Scope:
    name = normalize(re.sub(r"\b(?:province|of)\b", " ", heading, flags=re.I))
    if name in _ISLAND_GROUPS:
        return Scope("any", name)
    if _REGION_WORDS.search(heading):
        # "Region III Central Luzon" -> "central luzon"; "NCR" stays "ncr"
        cleaned = normalize(re.sub(r"\bregion\s+[ivx0-9-]+\b|\(.*?\)", " ", heading, flags=re.I))
        return Scope("region", cleaned or name)
    return Scope("province", name)


def _looks_like_heading(line: str) -> bool:
    """A short label on its own line: "Pampanga", "Region III Central Luzon", "Quezon Province:"."""
    text = line.rstrip(":").strip()
    if not text or len(text) > 45 or len(text.split()) > 6:
        return False
    if _NOISE.search(text) or re.search(r"[.!?()\d]{1}$|[()]", text) and not re.search(r"region", text, re.I):
        return False
    return text[0].isupper() and not _looks_like_details(text)


_PARTIAL_RE = re.compile(
    r"\b(?:the )?following schools\b|\bsome schools\b|\bselected schools\b|\bthese schools\b|\bcertain schools\b",
    re.I,
)


@dataclass
class Candidate:
    line: str
    location: Location
    levels: list[Level]
    school_type: SchoolType
    mode: Mode
    time_scope: TimeScope
    until_lifted: bool = False
    lifted_on: dt.date | None = None
    reason: Reason = Reason.WEATHER
    notes: list[str] = field(default_factory=list)
    # Filled in by import_parsed:
    action: str | None = None  # "created", "added_source", "already_listed"
    suspension_id: int | None = None


@dataclass
class ParseResult:
    candidates: list[Candidate]
    unmatched: list[str]


def _split_place(line: str) -> tuple[str, str] | None:
    """"Angeles City, Pampanga: all levels" -> ("Angeles City, Pampanga", "all levels")."""
    parts = _SPLIT_RE.split(line, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and len(parts[0]) <= 80:
        return parts[0].strip(" •*·-"), parts[1].strip()
    return None


def _looks_like_details(text: str) -> bool:
    return bool(_LEVEL_WORDS.search(text)) or bool(re.search(r"public|private|face[- ]to[- ]face", text, re.I))


def _resolve(index: PlaceIndex, place_text: str, scope: Scope | None) -> Location | None:
    """The Location a line names, if it fits the heading it's under.

    "San Fernando" under "Pangasinan" is San Fernando, La Union (or another
    town we don't know), not San Fernando, Pampanga, so it's rejected.
    """
    name, _, province = place_text.partition(",")
    if province.strip():
        explicit = Scope("province", normalize(province)) if not _REGION_WORDS.search(province) else scope_for(province)
        loc = index.lookup(name, province.strip())
        return loc if loc is not None and explicit.allows(loc) else None
    loc = index.lookup(place_text, scope.name if scope and scope.kind == "province" else None)
    if loc is None:
        return None
    if scope is not None and not scope.allows(loc):
        return None
    return loc


def parse_text(
    index: PlaceIndex,
    text: str,
    day: dt.date,
    default_reason: Reason = Reason.WEATHER,
    default_mode: Mode = Mode.ALL_CLASSES,
) -> ParseResult:
    """Find suspensions in announcement text. `day` is the date being suspended
    (used to read "until Friday, Sept. 11"). `default_mode` applies to lines that
    don't say, e.g. when the headline says "In-person classes suspended"."""
    lines = [line.strip(" \t•*·") for line in text.splitlines()]
    lines = [line for line in lines if line]
    candidates: list[Candidate] = []
    unmatched: list[str] = []
    seen: set[int] = set()
    region_scope: Scope | None = None
    scope: Scope | None = None
    # A place alone on its line, waiting for a details-only line under it (Inquirer style).
    pending: tuple[Location | None, str] | None = None

    for line in lines:
        if _NOISE.search(line) and len(line) < 60:
            continue
        split = _split_place(line)

        if split is None:
            if pending is not None and _looks_like_details(line) and not _looks_like_heading(line):
                location, details = pending[0], line
                line = f"{pending[1]}: {line}"
                pending = None
                if location is None:
                    unmatched.append(line)
                    continue
            elif _looks_like_heading(line):
                heading = line.rstrip(":").strip()
                new_scope = scope_for(heading)
                if new_scope.kind in ("region", "any"):
                    region_scope = new_scope if new_scope.kind == "region" else None
                    scope = region_scope
                else:
                    scope = new_scope
                # The heading may itself be a place ("Metro Manila", "Angeles, Pampanga"),
                # checked against the region it sits in.
                pending = (_resolve(index, heading, region_scope), heading)
                continue
            else:
                pending = None
                continue
        else:
            place_text, details = split
            pending = None
            if re.search(r",\s*philippines$", place_text, re.I):  # "MANILA, Philippines —" dateline
                continue
            location = _resolve(index, place_text, scope)
            if location is None:
                if _looks_like_details(details):
                    unmatched.append(line)
                continue

        if not _looks_like_details(details) or location.id in seen:
            continue
        if _PARTIAL_RE.search(details):
            # "San Simon: The following schools are switching to online classes" is
            # about particular schools, not the whole town. A person decides.
            unmatched.append(f"{line} [only some schools]")
            continue

        seen.add(location.id)
        candidate = Candidate(
            line=line,
            location=location,
            levels=parse_levels(details),
            school_type=parse_school_type(details),
            mode=parse_mode(details) if parse_mode(details) is Mode.FACE_TO_FACE_ONLY else default_mode,
            time_scope=parse_time_scope(details),
            reason=parse_reason(details, default_reason),
        )
        until = _UNTIL_RE.search(details)
        if until:
            end = find_date(until.group(1) or until.group(2) or "", day)
            if end is not None and end >= day and candidate.time_scope is TimeScope.WHOLE_DAY:
                candidate.until_lifted = True
                candidate.lifted_on = end + dt.timedelta(days=1)  # the day classes resume
            elif re.search(r"further notice|lifted", details, re.I):
                candidate.until_lifted = candidate.time_scope is TimeScope.WHOLE_DAY
            else:
                candidate.notes.append("Has an 'until' date that couldn't be read; check it.")
        elif find_date(details, day) not in (None, day) or re.search(
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\s*(?:and|&|[-–]|to)\s*\d{1,2}\b",
            details, re.I,
        ):
            candidate.notes.append("Mentions another date; it may cover more than one day.")
        if re.search(r"\bexcept\b", details, re.I):
            candidate.notes.append("Has exceptions; check the original.")
        candidates.append(candidate)

    return ParseResult(candidates=candidates, unmatched=unmatched)


def parse_whole(
    location: Location,
    text: str,
    day: dt.date,
    default_reason: Reason = Reason.WEATHER,
    default_mode: Mode = Mode.ALL_CLASSES,
) -> ParseResult:
    """Read a whole announcement written as a paragraph as one suspension for
    `location`, e.g. "suspendido ang pasok sa lahat ng antas ... simula 1:00 PM"."""
    flat = " ".join(text.split())
    mode = parse_mode(flat)
    candidate = Candidate(
        line=flat[:600],
        location=location,
        levels=parse_levels(flat),
        school_type=parse_school_type(flat),
        mode=mode if mode is Mode.FACE_TO_FACE_ONLY else default_mode,
        time_scope=parse_time_scope(flat),
        reason=parse_reason(flat, default_reason),
        notes=["Read from a paragraph, not a list; check the details."],
    )
    return ParseResult(candidates=[candidate], unmatched=[])


# --- Saving ------------------------------------------------------------------


@dataclass
class SourceInfo:
    url: str
    name: str
    kind: SourceKind | None = None
    posted_at: dt.datetime | None = None
    reason_detail: str | None = None


def _existing(db: Session, location: Location, day: dt.date) -> Suspension | None:
    return db.scalar(
        select(Suspension)
        .where(Suspension.location_id == location.id, Suspension.date == day)
        .order_by(Suspension.id)
        .limit(1)
    )


def import_parsed(db: Session, result: ParseResult, day: dt.date, source: SourceInfo) -> list[Candidate]:
    """Save candidates as pending suspensions, or add the article to the proof of
    one that already exists for that place and day."""
    kind = source.kind or infer_kind(source.url)
    for c in result.candidates:
        existing = _existing(db, c.location, day)
        if existing is not None:
            known = {p.url for p in existing.proof}
            if source.url in known:
                c.action, c.suspension_id = "already_listed", existing.id
                continue
            existing.extra_sources.append(
                SuspensionSource(kind=kind, name=source.name, url=source.url, excerpt=c.line,
                                 posted_at=clock.to_utc(source.posted_at))
            )
            db.commit()
            c.action, c.suspension_id = "added_source", existing.id
            continue

        data = SuspensionCreate(
            location=c.location.slug,
            date=day,
            levels=c.levels,
            school_type=c.school_type,
            mode=c.mode,
            time_scope=c.time_scope,
            until_lifted=c.until_lifted,
            reason=c.reason,
            reason_detail=source.reason_detail,
            source_url=source.url,
            source_name=source.name,
            source_kind=kind,
            source_excerpt=c.line,
            source_posted_at=source.posted_at,
        )
        created = suspension_service.create(db, data)
        if c.lifted_on is not None:
            created.lifted_on = c.lifted_on
            db.commit()
        c.action, c.suspension_id = "created", created.id
    return result.candidates
