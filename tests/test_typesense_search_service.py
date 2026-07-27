# """
# Search service unit tests.

# `build_search_params` and friends are pure, so the entire query-construction
# layer is testable without a TypeSense node. That matters because a wrong
# `filter_by` string does not error — TypeSense happily returns zero results, and
# "search returns nothing for California" is a bug you find in production.

# Run: pytest tests/test_typesense_search_service.py -v
# """
# import pytest

# from app.services.typesense_search_service import (
#     MAX_RESULT_WINDOW,
#     build_filter_by,
#     build_query,
#     build_search_params,
#     build_sort_by,
#     _escape_filter_value,
#     _to_card_dict,
# )


# class TestFilterBy:
#     def test_empty_when_no_filters(self):
#         assert build_filter_by() == ""

#     def test_state_is_uppercased(self):
#         """The mapper stores state uppercase; filter_by equality is case-sensitive."""
#         assert build_filter_by(state="ca") == "state:=`CA`"

#     def test_multiple_filters_are_anded(self):
#         result = build_filter_by(state="CA", zip_code="94559")
#         assert result == "state:=`CA` && zip_code:=`94559`"

#     def test_category_value_with_spaces_and_slash(self):
#         """The real category values contain both — they must survive intact."""
#         result = build_filter_by(facility_type_category="Nursing Home / Skilled Nursing Facility")
#         assert result == "facility_type_category:=`Nursing Home / Skilled Nursing Facility`"

#     @pytest.mark.parametrize("payload", ["CA` || state:=`NY", "a && b", "x) || (y"])
#     def test_filter_syntax_cannot_be_injected(self, payload):
#         """
#         `&&`, `||`, `(`, `)` are filter grammar. An unescaped value containing
#         them rewrites the filter — the search-engine equivalent of SQL injection.
#         """
#         escaped = _escape_filter_value(payload)
#         assert escaped.startswith("`") and escaped.endswith("`")
#         assert "`" not in escaped[1:-1]


# class TestQueryBuilding:
#     def test_no_text_params_is_match_all(self):
#         q, query_by, infix = build_query()
#         assert q == "*"
#         assert infix == "off"

#     def test_city_only_is_scoped_to_city(self):
#         """
#         Searching city across every field would surface 'Napa Street, Los
#         Angeles' for city=Napa — a precision regression vs Postgres.
#         """
#         q, query_by, _ = build_query(city="Napa")
#         assert q == "Napa"
#         assert query_by == "city"

#     def test_name_only_is_scoped_to_name(self):
#         q, query_by, _ = build_query(name="Sunrise")
#         assert q == "Sunrise"
#         assert query_by == "name"

#     def test_both_targets_both_fields(self):
#         q, query_by, _ = build_query(name="Sunrise", city="Napa")
#         assert q == "Sunrise Napa"
#         assert query_by == "name,city"

#     def test_q_param_searches_all_fields(self):
#         """The additive `q` param is what lets the client drop its retry hack."""
#         q, query_by, _ = build_query(q="sunrise napa")
#         assert query_by == "name,city,address,state"

#     def test_q_takes_precedence_over_name_and_city(self):
#         q, query_by, _ = build_query(q="explicit", name="ignored", city="ignored")
#         assert q == "explicit"

#     def test_whitespace_only_is_treated_as_absent(self):
#         q, _, _ = build_query(name="   ", city="   ")
#         assert q == "*"

#     def test_infix_enabled_for_text_queries(self):
#         """Infix is what makes 'ospice' match 'Hospice'; prefix search cannot."""
#         _, _, infix = build_query(name="ospice")
#         assert "fallback" in infix


# class TestSortBy:
#     def test_relevance_leads_when_there_is_a_text_query(self):
#         """A name match must outrank a higher-rated facility that matched on address."""
#         assert build_sort_by(True).startswith("_text_match:desc")

#     def test_matches_postgres_ordering_without_a_query(self):
#         """Mirrors `ORDER BY overall_rating DESC NULLS LAST, name`."""
#         assert build_sort_by(False) == "rating_sort:desc,name:asc"

#     @pytest.mark.parametrize("has_query", [True, False])
#     def test_ordering_is_total(self, has_query):
#         """Without a tie-break the same doc can appear on two pages."""
#         assert build_sort_by(has_query).endswith("name:asc")


# class TestSearchParams:
#     def test_no_none_values_are_sent(self):
#         """TypeSense rejects unknown/None parameter values outright."""
#         params = build_search_params(
#             q=None, name=None, city=None, state=None, zip_code=None,
#             facility_type=None, facility_type_category=None, page=1, page_size=20,
#         )
#         assert None not in params.values()

#     def test_filter_by_omitted_when_there_are_no_filters(self):
#         params = build_search_params(
#             q=None, name="Sunrise", city=None, state=None, zip_code=None,
#             facility_type=None, facility_type_category=None, page=1, page_size=20,
#         )
#         assert "filter_by" not in params

#     def test_include_fields_matches_the_facility_card_contract(self):
#         """Anything not listed here cannot be returned by the endpoint."""
#         params = build_search_params(
#             q="x", name=None, city=None, state=None, zip_code=None,
#             facility_type=None, facility_type_category=None, page=1, page_size=20,
#         )
#         for field in (
#             "id", "name", "facility_type", "facility_type_category", "city",
#             "state", "zip_code", "latitude", "longitude", "overall_rating",
#             "bed_count", "ownership_type",
#         ):
#             assert field in params["include_fields"]

#     def test_pagination_passes_through(self):
#         params = build_search_params(
#             q=None, name=None, city=None, state="CA", zip_code=None,
#             facility_type=None, facility_type_category=None, page=3, page_size=50,
#         )
#         assert params["page"] == 3
#         assert params["per_page"] == 50


# class TestCardMapping:
#     def test_absent_fields_become_explicit_none(self):
#         """FacilityCard declares them Optional; be explicit so drift raises."""
#         card = _to_card_dict({"document": {"id": "abc", "name": "X"}})
#         assert card["id"] == "abc"
#         assert card["overall_rating"] is None
#         assert card["bed_count"] is None

#     def test_key_set_is_exactly_the_card_contract(self):
#         card = _to_card_dict({"document": {}})
#         assert set(card) == {
#             "id", "name", "facility_type", "facility_type_category", "city",
#             "state", "zip_code", "latitude", "longitude", "overall_rating",
#             "bed_count", "ownership_type",
#         }


# class TestResultWindow:
#     def test_window_limit_is_documented(self):
#         """TypeSense rejects page*per_page beyond this; the endpoint guards it."""
#         assert MAX_RESULT_WINDOW == 10_000








































"""
Search service unit tests.

`build_search_params` and friends are pure, so the entire query-construction
layer is testable without a TypeSense node. That matters because a wrong
`filter_by` string does not error — TypeSense happily returns zero results, and
"search returns nothing for California" is a bug you find in production.

Run: pytest tests/test_typesense_search_service.py -v
"""
import pytest

from app.services.typesense_collection_service import (
    FIELD_NUM_TYPOS,
    INFIX_FIELDS,
    QUERY_BY_FIELDS,
    QUERY_BY_WEIGHTS,
)
from app.services.typesense_search_service import (
    MAX_RESULT_WINDOW,
    build_filter_by,
    build_query,
    build_search_params,
    build_sort_by,
    _escape_filter_value,
    _field_params,
    _to_card_dict,
)


class TestFilterBy:
    def test_empty_when_no_filters(self):
        assert build_filter_by() == ""

    def test_state_is_uppercased(self):
        """The mapper stores state uppercase; filter_by equality is case-sensitive."""
        assert build_filter_by(state="ca") == "state:=`CA`"

    def test_multiple_filters_are_anded(self):
        result = build_filter_by(state="CA", zip_code="94559")
        assert result == "state:=`CA` && zip_code:=`94559`"

    def test_category_value_with_spaces_and_slash(self):
        """The real category values contain both — they must survive intact."""
        result = build_filter_by(facility_type_category="Nursing Home / Skilled Nursing Facility")
        assert result == "facility_type_category:=`Nursing Home / Skilled Nursing Facility`"

    @pytest.mark.parametrize("payload", ["CA` || state:=`NY", "a && b", "x) || (y"])
    def test_filter_syntax_cannot_be_injected(self, payload):
        """
        `&&`, `||`, `(`, `)` are filter grammar. An unescaped value containing
        them rewrites the filter — the search-engine equivalent of SQL injection.
        """
        escaped = _escape_filter_value(payload)
        assert escaped.startswith("`") and escaped.endswith("`")
        assert "`" not in escaped[1:-1]


class TestFieldConfigConsistency:
    """
    These guard the positional alignment between QUERY_BY_FIELDS and everything
    derived from it. A mismatch is a TypeSense runtime error on one specific
    code path — the kind of bug that ships.
    """

    def test_weights_align_with_fields(self):
        assert len(QUERY_BY_WEIGHTS) == len(QUERY_BY_FIELDS)

    def test_every_searchable_field_has_a_typo_setting(self):
        assert set(FIELD_NUM_TYPOS) >= set(QUERY_BY_FIELDS)

    def test_infix_fields_are_a_subset_of_searchable_fields(self):
        """Requesting infix on a field not declared `infix: true` is an error."""
        assert INFIX_FIELDS <= set(QUERY_BY_FIELDS)

    def test_infix_fields_are_declared_in_the_schema(self):
        from app.services.typesense_collection_service import build_schema

        declared = {f["name"] for f in build_schema()["fields"] if f.get("infix")}
        assert INFIX_FIELDS == declared

    @pytest.mark.parametrize("fields", [["name"], ["name", "city"], list(QUERY_BY_FIELDS)])
    def test_all_derived_strings_have_equal_arity(self, fields):
        derived = _field_params(fields)
        lengths = {key: len(val.split(",")) for key, val in derived.items()}
        assert set(lengths.values()) == {len(fields)}, lengths


class TestTypoTolerance:
    def test_state_and_zip_refuse_typos(self):
        """
        'CA'/'GA' and '94559'/'94558' are one edit apart and are real, different
        places. A typo-tolerant match here returns confidently wrong results.
        """
        assert FIELD_NUM_TYPOS["state"] == 0
        assert FIELD_NUM_TYPOS["zip_code"] == 0

    def test_name_and_city_tolerate_typos(self):
        assert FIELD_NUM_TYPOS["name"] == 2
        assert FIELD_NUM_TYPOS["city"] == 2

    def test_per_field_typos_land_in_the_request(self):
        params = build_search_params(
            q="94559", name=None, city=None, state=None, zip_code=None,
            facility_type=None, facility_type_category=None, page=1, page_size=20,
        )
        assert params["num_typos"] == "2,2,1,0,0"


class TestQueryBuilding:
    def test_no_text_params_is_match_all(self):
        q, fields = build_query()
        assert q == "*"
        assert fields == ["name"]

    def test_city_only_is_scoped_to_city(self):
        """
        Searching city across every field would surface 'Napa Street, Los
        Angeles' for city=Napa — a precision regression vs Postgres.
        """
        q, fields = build_query(city="Napa")
        assert q == "Napa"
        assert fields == ["city"]

    def test_name_only_is_scoped_to_name(self):
        q, fields = build_query(name="Sunrise")
        assert q == "Sunrise"
        assert fields == ["name"]

    def test_both_targets_both_fields(self):
        q, fields = build_query(name="Sunrise", city="Napa")
        assert q == "Sunrise Napa"
        assert fields == ["name", "city"]

    def test_q_param_searches_every_field(self):
        """The additive `q` param is what lets the client drop its retry hack."""
        _, fields = build_query(q="sunrise napa")
        assert fields == list(QUERY_BY_FIELDS)

    def test_q_covers_zip_code(self):
        """A user typing a ZIP into the single search box must get results."""
        _, fields = build_query(q="94559")
        assert "zip_code" in fields

    def test_q_takes_precedence_over_name_and_city(self):
        q, _ = build_query(q="explicit", name="ignored", city="ignored")
        assert q == "explicit"

    def test_whitespace_only_is_treated_as_absent(self):
        q, _ = build_query(name="   ", city="   ")
        assert q == "*"

    def test_infix_enabled_for_name_queries(self):
        """Infix is what makes 'ospice' match 'Hospice'; prefix search cannot."""
        _, fields = build_query(name="ospice")
        assert _field_params(fields)["infix"] == "fallback"


class TestSortBy:
    def test_relevance_leads_when_there_is_a_text_query(self):
        """A name match must outrank a higher-rated facility that matched on address."""
        assert build_sort_by(True).startswith("_text_match:desc")

    def test_matches_postgres_ordering_without_a_query(self):
        """Mirrors `ORDER BY overall_rating DESC NULLS LAST, name`."""
        assert build_sort_by(False) == "rating_sort:desc,name:asc"

    @pytest.mark.parametrize("has_query", [True, False])
    def test_ordering_is_total(self, has_query):
        """Without a tie-break the same doc can appear on two pages."""
        assert build_sort_by(has_query).endswith("name:asc")


class TestSearchParams:
    def test_no_none_values_are_sent(self):
        """TypeSense rejects unknown/None parameter values outright."""
        params = build_search_params(
            q=None, name=None, city=None, state=None, zip_code=None,
            facility_type=None, facility_type_category=None, page=1, page_size=20,
        )
        assert None not in params.values()

    def test_weights_are_always_sent(self):
        """Derived per-field now, so they apply to scoped queries too."""
        params = build_search_params(
            q=None, name="Sunrise", city=None, state=None, zip_code=None,
            facility_type=None, facility_type_category=None, page=1, page_size=20,
        )
        assert params["query_by_weights"] == "4"

    def test_filter_by_omitted_when_there_are_no_filters(self):
        params = build_search_params(
            q=None, name="Sunrise", city=None, state=None, zip_code=None,
            facility_type=None, facility_type_category=None, page=1, page_size=20,
        )
        assert "filter_by" not in params

    def test_include_fields_matches_the_facility_card_contract(self):
        """Anything not listed here cannot be returned by the endpoint."""
        params = build_search_params(
            q="x", name=None, city=None, state=None, zip_code=None,
            facility_type=None, facility_type_category=None, page=1, page_size=20,
        )
        for field in (
            "id", "name", "facility_type", "facility_type_category", "city",
            "state", "zip_code", "latitude", "longitude", "overall_rating",
            "bed_count", "ownership_type",
        ):
            assert field in params["include_fields"]

    def test_pagination_passes_through(self):
        params = build_search_params(
            q=None, name=None, city=None, state="CA", zip_code=None,
            facility_type=None, facility_type_category=None, page=3, page_size=50,
        )
        assert params["page"] == 3
        assert params["per_page"] == 50


class TestCardMapping:
    def test_absent_fields_become_explicit_none(self):
        """FacilityCard declares them Optional; be explicit so drift raises."""
        card = _to_card_dict({"document": {"id": "abc", "name": "X"}})
        assert card["id"] == "abc"
        assert card["overall_rating"] is None
        assert card["bed_count"] is None

    def test_key_set_is_exactly_the_card_contract(self):
        card = _to_card_dict({"document": {}})
        assert set(card) == {
            "id", "name", "facility_type", "facility_type_category", "city",
            "state", "zip_code", "latitude", "longitude", "overall_rating",
            "bed_count", "ownership_type",
        }


class TestResultWindow:
    def test_window_limit_is_documented(self):
        """TypeSense rejects page*per_page beyond this; the endpoint guards it."""
        assert MAX_RESULT_WINDOW == 10_000