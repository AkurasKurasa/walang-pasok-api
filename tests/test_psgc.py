"""The PSGC snapshot becomes every location, with the right parents and stable slugs."""

import pytest

from app.models import LocationType
from app.services import psgc
from scripts.seed import LOCATIONS


@pytest.fixture(scope="module")
def rows():
    overrides = {loc.psgc_code: (loc.name, loc.slug) for loc in LOCATIONS}
    built = psgc.build(psgc.load(), overrides)
    return {r.slug: r for r in built}, {r.psgc_code: r for r in built}, built


def parent_slug(rows, slug):
    by_slug, by_code, _ = rows
    return by_code[by_slug[slug].parent_code].slug


def test_covers_the_whole_country(rows):
    by_slug, _, built = rows
    counts = {t: sum(1 for r in built if r.type is t) for t in LocationType}
    assert counts[LocationType.COUNTRY] == 1  # "philippines", above the regions
    assert counts[LocationType.REGION] == 17
    assert counts[LocationType.PROVINCE] == 81
    assert counts[LocationType.CITY] + counts[LocationType.MUNICIPALITY] == 1634
    assert len(by_slug) == len(built)  # every slug unique


def test_parents_come_before_children(rows):
    _, _, built = rows
    seen = set()
    for r in built:
        assert r.parent_code is None or r.parent_code in seen
        seen.add(r.psgc_code)


def test_existing_slugs_and_names_are_kept(rows):
    by_slug, _, _ = rows
    for loc in LOCATIONS:
        assert by_slug[loc.slug].psgc_code == loc.psgc_code
        assert by_slug[loc.slug].name == loc.name


def test_independent_city_belongs_to_the_region(rows):
    # Angeles is highly urbanized: a Pampanga-wide suspension doesn't cover it.
    assert parent_slug(rows, "angeles-city") == "central-luzon"
    assert rows[0]["angeles-city"].province == "Pampanga"  # still searchable by province
    assert parent_slug(rows, "quezon-city") == "metro-manila"


def test_component_city_belongs_to_the_province(rows):
    assert parent_slug(rows, "mabalacat-city") == "pampanga"
    assert parent_slug(rows, "san-jose-del-monte-city") == "bulacan-province"
    assert parent_slug(rows, "mamburao") == "occidental-mindoro"


def test_same_names_get_their_province(rows):
    by_slug, _, _ = rows
    assert by_slug["san-juan-batangas"].province == "Batangas"
    assert by_slug["san-juan-la-union"].province == "La Union"
    # A province named like one of its towns gets "-province".
    assert by_slug["bulacan-province"].type is LocationType.PROVINCE
    assert by_slug["bulacan"].type is LocationType.MUNICIPALITY


def test_city_names_read_naturally():
    assert psgc.display_name("City of Imus") == "Imus City"
    assert psgc.display_name("City of Manila") == "City of Manila"
    assert psgc.display_name("Mamburao") == "Mamburao"
    assert psgc.slugify("Parañaque City") == "paranaque-city"
