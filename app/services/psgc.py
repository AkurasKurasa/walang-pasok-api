"""Every region, province, city and municipality in the Philippines, from the PSGC.

The data in `app/data/psgc/` is a snapshot of https://psgc.gitlab.io/api/, a
JSON copy of the PSA's Philippine Standard Geographic Code. Refresh it with
`python -m scripts.seed --refresh-psgc`.

`build()` turns it into location rows (no database access), deciding three
things the raw data doesn't spell out:

* **Parent** (which wider suspensions apply). A component city or municipality
  belongs to its province. A highly urbanized or independent component city
  (Angeles, Dagupan, every NCR city) is independent of its province, so its
  parent is the region. The PSGC code shows which is which: a component LGU's
  code repeats its province's code (Mabalacat 03-054-09-000 is under Pampanga
  03-054), an independent city's doesn't (Angeles 03-301-00-000).
* **Name** as people write it: "City of Imus" becomes "Imus City".
* **Slug**: the name, plus the province when the name is used more than once
  nationwide ("san-juan-batangas", "san-juan-la-union").
"""

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.models import LocationType

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "psgc"
API_BASE = "https://psgc.gitlab.io/api/"
FILES = ("regions", "provinces", "cities-municipalities")

# The PSGC has no code for the country itself; this all-zero code is our own.
COUNTRY_CODE = "0000000000"
COUNTRY_SLUG = "philippines"

# Names whose "City of ..." form is how people actually say them.
_KEEP_CITY_OF = {"City of Manila", "City of San Fernando"}


@dataclass(frozen=True)
class PlaceRow:
    psgc_code: str  # 10-digit
    name: str
    slug: str
    type: LocationType
    region: str
    province: str | None  # geographic province, for display and search
    parent_code: str | None  # 10-digit code of the parent row


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def display_name(name: str) -> str:
    """ "City of Imus" -> "Imus City"; leaves municipalities and well-known forms alone."""
    m = re.match(r"^City of (.+)$", name)
    if m and name not in _KEEP_CITY_OF:
        return f"{m.group(1)} City"
    return name


def load(data_dir: Path = DATA_DIR) -> dict[str, list[dict]]:
    return {name: json.loads((data_dir / f"{name}.json").read_text(encoding="utf-8")) for name in FILES}


def download(data_dir: Path = DATA_DIR) -> dict[str, int]:
    """Refresh the snapshot from the PSGC API. Returns row counts per file."""
    import urllib.request

    data_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name in FILES:
        request = urllib.request.Request(f"{API_BASE}{name}/", headers={"User-Agent": "walang-pasok-psgc-refresh"})
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)
        (data_dir / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
        counts[name] = len(rows)
    return counts


def _region_label(region: dict) -> str:
    name, short = region["name"], region.get("regionName") or ""
    return f"{name} ({short})" if short and short not in name else name


def build(data: dict[str, list[dict]], overrides: dict[str, tuple[str, str]] | None = None) -> list[PlaceRow]:
    """Rows for every place, parents before children.

    `overrides` maps a 10-digit code to a (name, slug) to keep, so places that
    already have a URL keep it.
    """
    overrides = overrides or {}
    regions = {r["code"]: r for r in data["regions"]}
    provinces = {p["code"]: p for p in data["provinces"]}
    localities = data["cities-municipalities"]

    used: set[str] = {slug for _, slug in overrides.values()}

    def unique(slug: str, *fallbacks: str) -> str:
        for candidate in (slug, *(f"{slug}-{f}" for f in fallbacks if f)):
            if candidate not in used:
                used.add(candidate)
                return candidate
        n = 2
        while f"{slug}-{n}" in used:
            n += 1
        used.add(f"{slug}-{n}")
        return f"{slug}-{n}"

    def named(code: str, name: str, make_slug) -> tuple[str, str]:
        if code in overrides:
            return overrides[code]
        return name, make_slug()

    # The whole country sits above the regions, so a nationwide holiday covers every place.
    used.add(COUNTRY_SLUG)
    rows: list[PlaceRow] = [
        PlaceRow(COUNTRY_CODE, "Philippines", COUNTRY_SLUG, LocationType.COUNTRY, "Philippines", None, None)
    ]

    for r in data["regions"]:
        code = r["psgc10DigitCode"]
        name, slug = named(code, _region_label(r), lambda: unique(slugify(r["name"])))
        rows.append(PlaceRow(code, name, slug, LocationType.REGION, _region_label(r), None, COUNTRY_CODE))

    locality_slugs = Counter(slugify(display_name(c["name"])) for c in localities)
    for p in data["provinces"]:
        code = p["psgc10DigitCode"]
        region = regions[p["regionCode"]]
        base = slugify(p["name"])
        # A province named like a town ("Aurora", "Bataan" vs Bataan the town) gets "-province".
        name, slug = named(code, p["name"], lambda: unique(f"{base}-province" if base in locality_slugs else base))
        rows.append(PlaceRow(code, name, slug, LocationType.PROVINCE, _region_label(region), p["name"],
                             region["psgc10DigitCode"]))

    name_counts = Counter(display_name(c["name"]) for c in localities)
    for c in localities:
        code = c["psgc10DigitCode"]
        region = regions[c["regionCode"]]
        province = provinces.get(c["provinceCode"]) if c["provinceCode"] else None
        label = display_name(c["name"])

        def make_slug() -> str:
            base = slugify(label)
            where = slugify(province["name"]) if province else slugify(region["name"])
            if name_counts[label] > 1:
                return unique(f"{base}-{where}")
            return unique(base, where)

        name, slug = named(code, label, make_slug)
        component = province is not None and code[2:5] == province["psgc10DigitCode"][2:5]
        parent = province["psgc10DigitCode"] if component else region["psgc10DigitCode"]
        rows.append(PlaceRow(
            code, name, slug,
            LocationType.CITY if c["isCity"] else LocationType.MUNICIPALITY,
            _region_label(region), province["name"] if province else None, parent,
        ))
    return rows
