"""
Mapper unit tests.

No DB, no TypeSense — the mapper is pure, so these run in milliseconds and are
the cheapest place to catch a source-data change. If the upstream pipeline ever
starts sending a new shape (a differently formatted number, a new placeholder
token), these fail before 60,000 bad documents reach the index.

Run: pytest tests/test_facility_mapper.py -v
"""
import pytest

from app.utils.facility_mapper import (
    MappingError,
    build_all_ids_sql,
    build_count_sql,
    build_select_sql,
    clean_str,
    to_document,
    to_documents,
    to_epoch_seconds,
    to_float,
    to_int,
)

# A verbatim row from public."All_State_Type_combined" (trimmed to mapped cols).
REAL_ROW = {
    "uuid": "4174ec0a-bd07-484b-9db9-722145000178",
    "ccn": "550006277",
    "name": "SAVIOR HOSPICE CARE, INC.",
    "facility_type": "Hospice",
    "facility_type_category": "Hospice",
    "legal_business_name": "",
    "ownership_type": "",
    "address": "2209 JEFFERSON ST",
    "city": "NAPA",
    "state": "CA",
    "zip_code": "94559",
    "latitude": "38.3071606",
    "longitude": "-122.2956606",
    "bed_count": "",
    "overall_rating": "",
    "updated_at": "2026-07-22 09:39:30.972483",
}


class TestCleanStr:
    @pytest.mark.parametrize(
        "raw", ["", "   ", "unknown", "UNKNOWN", "N/A", "n/a", "null", "-", "nan"]
    )
    def test_placeholders_become_none(self, raw):
        assert clean_str(raw) is None

    def test_trims_whitespace(self):
        assert clean_str("  NAPA  ") == "NAPA"


class TestNumericCoercion:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("38.3071606", 38.3071606), ("4.5", 4.5), ("1,200", 1200.0), ("$500", 500.0), ("", None), ("N/A", None)],
    )
    def test_to_float(self, raw, expected):
        assert to_float(raw) == expected

    @pytest.mark.parametrize(("raw", "expected"), [("120", 120), ("120.0", 120), ("1,200", 1200), ("", None)])
    def test_to_int(self, raw, expected):
        assert to_int(raw) == expected

    def test_int32_overflow_rejected(self):
        """TypeSense int32 would reject the document — drop the field instead."""
        assert to_int("9999999999") is None

    def test_nan_and_inf_rejected(self):
        """NaN/inf are valid floats but invalid JSON — they'd break serialization."""
        assert to_float("nan") is None
        assert to_float("inf") is None


class TestTimestamps:
    def test_naive_timestamp_treated_as_utc(self):
        assert to_epoch_seconds("2026-07-22 09:39:30.972483") == 1784713170

    def test_iso_with_offset(self):
        assert to_epoch_seconds("2026-07-22T09:39:30+00:00") == 1784713170

    def test_garbage_returns_none(self):
        assert to_epoch_seconds("not a date") is None


class TestToDocument:
    def test_maps_a_real_row(self):
        doc = to_document(REAL_ROW)
        assert doc["id"] == REAL_ROW["uuid"]
        assert doc["name"] == "SAVIOR HOSPICE CARE, INC."
        assert doc["city"] == "NAPA"
        assert doc["state"] == "CA"
        assert doc["latitude"] == pytest.approx(38.3071606)
        assert doc["longitude"] == pytest.approx(-122.2956606)

    def test_absent_fields_are_omitted_not_null(self):
        """
        TypeSense treats an explicit null as a type error on an optional field,
        but an absent key as 'no value'. This distinction is the single most
        common cause of mass import rejection.
        """
        doc = to_document(REAL_ROW)
        assert "ownership_type" not in doc
        assert "bed_count" not in doc
        assert "overall_rating" not in doc
        assert None not in doc.values()

    def test_rating_sort_always_present(self):
        """Backs default_sorting_field — TypeSense rejects the collection without it."""
        assert to_document(REAL_ROW)["rating_sort"] == 0.0
        assert to_document({**REAL_ROW, "overall_rating": "4.5"})["rating_sort"] == 4.5

    def test_state_is_uppercased(self):
        """filter_by string equality is case-sensitive, unlike search matching."""
        assert to_document({**REAL_ROW, "state": " ca "})["state"] == "CA"

    @pytest.mark.parametrize(
        ("lat", "lng"),
        [("", ""), ("38.5", ""), ("", "-122.3"), ("999", "-122.3"), ("38.5", "999"), ("0", "0")],
    )
    def test_bad_coordinates_dropped_as_a_pair(self, lat, lng):
        """A lone or out-of-range coordinate renders a broken map pin."""
        doc = to_document({**REAL_ROW, "latitude": lat, "longitude": lng})
        assert "latitude" not in doc and "longitude" not in doc

    @pytest.mark.parametrize("bad", [{"name": "X"}, {"uuid": "u"}, {"uuid": "u", "name": "unknown"}])
    def test_unusable_rows_rejected(self, bad):
        with pytest.raises(MappingError):
            to_document(bad)


class TestBatch:
    def test_one_bad_row_does_not_kill_the_batch(self):
        docs, errors = to_documents([REAL_ROW, {"uuid": "no-name"}, {"name": "no-id"}])
        assert len(docs) == 1
        assert len(errors) == 2
        assert docs[0]["id"] == REAL_ROW["uuid"]


class TestQueryBuilder:
    @pytest.mark.parametrize("builder", [build_select_sql, build_count_sql, build_all_ids_sql])
    def test_table_identifier_is_always_quoted(self, builder):
        """Unquoted, Postgres folds the mixed-case name to lowercase and 42P01s."""
        assert 'public."All_State_Type_combined"' in builder()

    def test_select_uses_keyset_not_offset(self):
        """OFFSET paging over 60k rows skips and duplicates under concurrent writes."""
        sql = build_select_sql()
        assert "OFFSET" not in sql.upper()
        assert ":after_uuid" in sql
        assert 'ORDER BY "uuid"' in sql

    def test_select_supports_incremental_and_full(self):
        """Both :since and :after_uuid are nullable, so one statement covers every mode."""
        sql = build_select_sql()
        assert ":since" in sql
        assert ":limit" in sql
        assert "IS NULL OR" in sql

    def test_every_mapped_column_is_selected(self):
        """
        The mapper silently omits a field whose key is absent, so a column
        dropped from the SELECT would degrade the index with no error at all.
        """
        from app.utils.facility_mapper import SOURCE_COLUMNS

        sql = build_select_sql()
        for column in SOURCE_COLUMNS:
            assert f'"{column}"' in sql