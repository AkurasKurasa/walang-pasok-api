"""The importer, tested on lines copied from real articles (see scripts/real_examples.py)."""

import datetime as dt

import pytest

from app.models import Level, Location, LocationType, Mode, Reason, SchoolType, Status, Suspension, TimeScope
from app.services import importer
from tests.conftest import ADMIN_HEADERS

K12 = [Level.PRESCHOOL, Level.ELEMENTARY, Level.JUNIOR_HIGH, Level.SENIOR_HIGH]


def place(id_, name, slug, type_=LocationType.MUNICIPALITY, province="Pampanga"):
    return Location(id=id_, psgc_code=f"{id_:010d}", name=name, slug=slug, type=type_, region="R", province=province)


@pytest.fixture
def index():
    return importer.PlaceIndex([
        place(1, "Pampanga", "pampanga", LocationType.PROVINCE),
        place(2, "Angeles City", "angeles-city", LocationType.CITY),
        place(3, "City of San Fernando", "san-fernando-pampanga", LocationType.CITY),
        place(4, "Santo Tomas", "santo-tomas-pampanga"),
        place(5, "Santa Ana", "santa-ana-pampanga"),
        place(6, "Masantol", "masantol"),
        place(7, "City of Manila", "manila", LocationType.CITY, None),
        place(8, "Parañaque City", "paranaque-city", LocationType.CITY, None),
        place(9, "Metro Manila", "metro-manila", LocationType.REGION, None),
        place(10, "Santo Tomas", "santo-tomas-batangas", province="Batangas"),
        place(11, "Pasig City", "pasig-city", LocationType.CITY, None),
        place(12, "Quezon City", "quezon-city", LocationType.CITY, None),
    ])


def one(index, text, day=dt.date(2025, 7, 21), **kw):
    result = importer.parse_text(index, text, day, **kw)
    assert len(result.candidates) == 1, result
    return result.candidates[0]


class TestPlaces:
    def test_normalize(self):
        assert importer.normalize("Parañaque City") == "paranaque city"
        assert importer.normalize("Sta. Ana") == "santa ana"
        assert importer.normalize("Sto. Tomas") == "santo tomas"

    @pytest.mark.parametrize("written,slug", [
        ("Manila City", "manila"), ("City of Manila", "manila"), ("Paranaque", "paranaque-city"),
        ("Parañaque City", "paranaque-city"), ("San Fernando", "san-fernando-pampanga"),
        ("NCR", "metro-manila"), ("Angeles", "angeles-city"),
    ])
    def test_name_variants(self, index, written, slug):
        assert index.lookup(written).slug == slug

    def test_heading_picks_the_right_same_named_town(self, index):
        text = "Pampanga\nSto. Tomas - no face-to-face classes, daycare to senior high school, public and private"
        assert one(index, text).location.slug == "santo-tomas-pampanga"

    def test_place_comma_province(self, index):
        c = one(index, "Sto. Tomas, Batangas: all levels, public and private schools")
        assert c.location.slug == "santo-tomas-batangas"

    def test_ambiguous_without_hint_is_unmatched(self, index):
        result = importer.parse_text(index, "Sto. Tomas - all levels", dt.date(2025, 7, 21))
        assert result.candidates == [] and result.unmatched == ["Sto. Tomas - all levels"]

    def test_place_and_details_on_separate_lines(self, index):
        # Inquirer, Sept 1, 2023
        text = "Metro Manila\nAll levels, public and private schools\nAngeles, Pampanga\nAll levels, public and private schools"
        result = importer.parse_text(index, text, dt.date(2023, 9, 1))
        assert [c.location.slug for c in result.candidates] == ["metro-manila", "angeles-city"]
        assert result.candidates[1].line == "Angeles, Pampanga: All levels, public and private schools"

    def test_unknown_places_are_reported(self, index):
        result = importer.parse_text(index, "Benguet\nKibungan – no face-to-face classes for daycare", dt.date(2026, 9, 10))
        assert result.unmatched == ["Kibungan – no face-to-face classes for daycare"]

    def test_ignores_prose_and_headlines(self, index):
        text = "Tarlac City suspends Oct. 6 classes for Teachers' Day activities\nRead more: weather updates"
        result = importer.parse_text(index, text, dt.date(2026, 10, 6))
        assert result.candidates == []


class TestDetails:
    def test_all_levels_both(self, index):
        c = one(index, "Angeles City - no classes, all levels, public and private")
        assert (c.levels, c.school_type, c.mode, c.time_scope) == ([Level.ALL], SchoolType.BOTH, Mode.ALL_CLASSES, TimeScope.WHOLE_DAY)

    def test_range_and_public_only(self, index):
        c = one(index, "Angeles City, Pampanga: kindergarten to senior high school, public schools")
        assert c.levels == K12 and c.school_type is SchoolType.PUBLIC

    def test_preschool_to_high_school(self, index):
        assert one(index, "Quezon City - preschool to high school, public").levels == K12

    def test_face_to_face(self, index):
        c = one(index, "Pasig City - no face-to-face classes, kindergarten to senior high school, public and private")
        assert c.mode is Mode.FACE_TO_FACE_ONLY and c.levels == K12

    def test_afternoon(self, index):
        c = one(index, "Manila City - all levels, public and private (afternoon classes)")
        assert c.time_scope is TimeScope.AFTERNOON

    def test_from_1pm_is_afternoon(self, index):
        assert one(index, "Metro Manila - all levels, simula 1:00 PM").time_scope is TimeScope.AFTERNOON

    def test_until_a_date(self, index):
        c = one(index, "Pampanga\nSto. Tomas – no face-to-face classes; all levels; public and private (until Saturday, Sept. 12)",
                day=dt.date(2026, 9, 10))
        assert c.until_lifted and c.lifted_on == dt.date(2026, 9, 13)

    def test_exceptions_and_extra_dates_are_flagged(self, index):
        c = one(index, "Masantol - no face-to-face classes, all levels, public and private (July 21 and 22)")
        assert any("another date" in n for n in c.notes)
        c = one(index, "Masantol – all levels; public and private (except Concepcion Integrated School)")
        assert any("exceptions" in n for n in c.notes)

    def test_article_wide_in_person_mode(self, index):
        intro = "Face-to-face classes are suspended in several areas on Monday, October 5"
        mode = importer.article_mode("", intro)
        c = one(index, f"{intro}\nAngeles City, Pampanga: all levels", default_mode=mode)
        assert c.mode is Mode.FACE_TO_FACE_ONLY

    def test_reason_from_text(self):
        assert importer.parse_reason("In-person classes suspended for Nat'l Teachers' Day", Reason.WEATHER) is Reason.EVENT
        assert importer.parse_reason("Class suspensions due to Typhoon Kristine", Reason.WEATHER) is Reason.WEATHER


class TestDates:
    @pytest.mark.parametrize("text,expected", [
        ("WALANG PASOK: Class suspensions on Thursday, Sept. 10", dt.date(2026, 9, 10)),
        ("Class suspensions for Monday, July 21, 2025", dt.date(2025, 7, 21)),
        ("In-person classes suspended Oct. 5 for Nat'l Teachers' Day", dt.date(2026, 10, 5)),
        ("Walang pasok: Class suspensions on January 2", dt.date(2027, 1, 2)),
    ])
    def test_find_date(self, text, expected):
        assert importer.find_date(text, near=dt.date(2026, 10, 10)) == expected

    def test_no_date(self):
        assert importer.find_date("Walang pasok bukas", near=dt.date(2026, 10, 10)) is None


def test_html_to_page():
    page = importer.html_to_page(
        "<html><head><title>WALANG PASOK</title><script>var x='Angeles - all levels'</script></head>"
        "<body><nav>Home</nav><p><strong>Pampanga</strong></p><ul><li>Masantol &ndash; all levels</li></ul>"
        "<a href='/2301928/x'>Walang pasok</a></body></html>"
    )
    assert page.title == "WALANG PASOK"
    assert page.text.splitlines() == ["Pampanga", "Masantol – all levels", "Walang pasok"]
    assert page.links == [("/2301928/x", "Walang pasok")]


class TestImport:
    TEXT = "Angeles City - all levels, public and private\nMetro Manila\nQuezon City - preschool to senior high school, public"

    def body(self, **kw):
        return {"text": self.TEXT, "date": "2026-10-12", "source_url": "https://news.example.ph/a",
                "source_name": "Example News", **kw}

    def test_dry_run_saves_nothing(self, client, places, db):
        response = client.post("/api/v1/imports", json=self.body(), headers=ADMIN_HEADERS)

        assert response.status_code == 200
        items = response.json()["items"]
        assert [i["location"]["slug"] for i in items] == ["angeles-city", "quezon-city"]
        assert items[1]["school_type"] == "public" and items[1]["action"] is None
        assert db.query(Suspension).count() == 0

    def test_import_creates_pending_with_proof(self, client, places, db):
        response = client.post("/api/v1/imports", json=self.body(dry_run=False), headers=ADMIN_HEADERS)

        assert [i["action"] for i in response.json()["items"]] == ["created", "created"]
        s = db.query(Suspension).filter_by(location=places["angeles-city"]).one()
        assert s.status is Status.PENDING
        assert s.proof[0].excerpt == "Angeles City - all levels, public and private"
        assert s.proof[0].name == "Example News"

    def test_second_article_becomes_extra_proof(self, client, places, db):
        client.post("/api/v1/imports", json=self.body(dry_run=False), headers=ADMIN_HEADERS)
        again = client.post("/api/v1/imports", json=self.body(dry_run=False), headers=ADMIN_HEADERS).json()
        other = client.post(
            "/api/v1/imports",
            json=self.body(dry_run=False, source_url="https://other.example.ph/b", source_name="Other News"),
            headers=ADMIN_HEADERS,
        ).json()

        assert {i["action"] for i in again["items"]} == {"already_listed"}
        assert {i["action"] for i in other["items"]} == {"added_source"}
        assert db.query(Suspension).count() == 2
        proof = db.query(Suspension).filter_by(location=places["angeles-city"]).one().proof
        assert [p.name for p in proof] == ["Example News", "Other News"]

    def test_needs_admin(self, client, places):
        assert client.post("/api/v1/imports", json=self.body()).status_code == 401


def test_parse_whole_reads_a_paragraph(index):
    """Quezon City's real post for July 21, 2025 (Malacañang Memorandum Circular No. 88)."""
    text = (
        "#WALANGPASOK | Base sa Memorandum Circular No. 88 mula sa Malacañang, suspendido ang pasok "
        "sa lahat ng antas ng paaralan at tanggapan ng gobyerno sa buong Metro Manila ngayong Lunes, "
        "July 21, 2025, simula 1:00 PM dahil sa patuloy na sama ng panahon dulot ng #Habagat."
    )
    metro = next(loc for locs in index.by_key.values() for loc in locs if loc.slug == "metro-manila")

    [c] = importer.parse_whole(metro, text, dt.date(2025, 7, 21)).candidates

    assert c.levels == [Level.ALL]  # "lahat ng antas"
    assert c.time_scope is TimeScope.AFTERNOON  # "simula 1:00 PM"
    assert c.school_type is SchoolType.BOTH
    assert c.notes
