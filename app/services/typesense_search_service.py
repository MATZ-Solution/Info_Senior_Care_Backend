# """
# TypeSense search execution for the facilities index.

# Owns exactly one thing: turning the existing `/facilities/search` query
# parameters into a TypeSense request and turning the response back into the
# shape the API already returns. It does NOT own the HTTP endpoint, the fallback
# decision, or caching — those stay in the endpoint so the Postgres path remains
# readable and reviewable side by side.

# CONTRACT PRESERVATION
# ---------------------
# The endpoint's parameters and response schema do not change. What changes is
# matching quality:

# * Postgres today: `fuzzy_or_exact()` per column — substring OR pg_trgm
#   similarity. Each column is matched independently.
# * TypeSense: real typo tolerance (edit distance), prefix matching, infix
#   matching on name/city, and relevance ranking across fields.

# The practical difference is that TypeSense returns MORE results for a typo'd
# query and orders them better. Filters that were exact in Postgres (`state`,
# `zip_code`) stay exact here — a typo-tolerant state filter would be actively
# wrong, since "CA" and "GA" are one edit apart.
# """
# from __future__ import annotations

# import logging
# from typing import Any

# from app.core.config import settings
# from app.core.typesense import get_typesense_client, run_typesense
# from app.services.typesense_collection_service import (
#     QUERY_BY_FIELDS,
#     QUERY_BY_WEIGHTS,
# )

# logger = logging.getLogger("app.typesense.search")

# # TypeSense refuses a result window beyond this (page * per_page). Deep paging
# # past it is meaningless for a search UI anyway — nobody browses to page 500 —
# # but it must not 500 the endpoint.
# MAX_RESULT_WINDOW = 10_000

# # Edit distance allowed per token. 2 is TypeSense's default and the closest
# # behavioural match to the existing pg_trgm `word_similarity > 0.4` threshold.
# NUM_TYPOS = 2


# def _escape_filter_value(value: str) -> str:
#     """
#     Make a value safe inside a `filter_by` expression.

#     TypeSense's filter grammar treats `&&`, `||`, `(`, `)`, `,`, `:` and
#     backticks as syntax. A city called "Winston-Salem" is fine, but an
#     unescaped user string containing them changes the meaning of the filter —
#     the search-engine equivalent of SQL injection. Backtick-quoting the value
#     and stripping backticks from inside it removes the whole class.
#     """
#     return "`" + value.replace("`", "") + "`"


# def build_filter_by(
#     *,
#     state: str | None = None,
#     zip_code: str | None = None,
#     facility_type: str | None = None,
#     facility_type_category: str | None = None,
# ) -> str:
#     """
#     Structured, exact filters — the parts of the query that must not be fuzzy.

#     `state` is expected to be already normalized to a 2-letter abbreviation by
#     the caller (the endpoint runs the existing `normalize_state()` so
#     "California" keeps working). Values are upper-cased here to match how the
#     mapper stores them.
#     """
#     clauses: list[str] = []

#     if state:
#         clauses.append(f"state:={_escape_filter_value(state.strip().upper())}")
#     if zip_code:
#         clauses.append(f"zip_code:={_escape_filter_value(zip_code.strip())}")
#     if facility_type_category:
#         clauses.append(
#             f"facility_type_category:={_escape_filter_value(facility_type_category.strip())}"
#         )
#     if facility_type:
#         clauses.append(f"facility_type:={_escape_filter_value(facility_type.strip())}")

#     return " && ".join(clauses)


# def build_query(
#     *,
#     q: str | None = None,
#     name: str | None = None,
#     city: str | None = None,
# ) -> tuple[str, str, str]:
#     """
#     Decide the free-text query, which fields it runs against, and infix mode.

#     Returns:
#         (q, query_by, infix) ready to drop into the search params.

#     The field scoping matters. `city="Napa"` searched across every field would
#     surface a facility on "Napa Street" in Los Angeles — a precision regression
#     versus the current Postgres behaviour, which only ever looks at the city
#     column. So each parameter keeps its own target fields:

#         name only  -> query_by "name"
#         city only  -> query_by "city"
#         both       -> query_by "name,city"
#         q (new)    -> query_by all four, weighted

#     `q` is an ADDITIVE optional parameter. Nothing sends it today, so nothing
#     changes. It exists because the mobile client currently issues a second
#     request when a one-word search comes back empty (guess city, retry as
#     name) — see `searchFacilitiesSmart` in the app. TypeSense can OR across
#     fields in a single request, so once the client adopts `q` that retry and
#     its extra round trip disappear.
#     """
#     if q and q.strip():
#         return (
#             q.strip(),
#             ",".join(QUERY_BY_FIELDS),
#             # `fallback`: try the normal prefix/typo match first, only reach for
#             # the more expensive infix scan when that finds nothing.
#             "fallback,fallback,off,off",
#         )

#     terms: list[str] = []
#     fields: list[str] = []
#     infix: list[str] = []

#     if name and name.strip():
#         terms.append(name.strip())
#         fields.append("name")
#         infix.append("fallback")
#     if city and city.strip():
#         terms.append(city.strip())
#         fields.append("city")
#         infix.append("fallback")

#     if not terms:
#         # TypeSense's match-all. Used when the request is filters-only
#         # (e.g. "everything in CA"), which the browse screen does on load.
#         return "*", "name", "off"

#     return " ".join(terms), ",".join(fields), ",".join(infix)


# def build_sort_by(has_text_query: bool) -> str:
#     """
#     Ranking.

#     With a text query, relevance leads — a name match must beat a
#     higher-rated facility that only matched on address. `rating_sort` then
#     breaks relevance ties, and `name` breaks the remaining ones so pagination
#     is deterministic (without a total order, the same document can appear on
#     two pages).

#     Without a text query this reproduces the existing Postgres ordering
#     exactly: `ORDER BY overall_rating DESC NULLS LAST, name`. `rating_sort` is
#     0.0 for unrated facilities, which is what puts them last.
#     """
#     if has_text_query:
#         return "_text_match:desc,rating_sort:desc,name:asc"
#     return "rating_sort:desc,name:asc"


# def build_search_params(
#     *,
#     q: str | None,
#     name: str | None,
#     city: str | None,
#     state: str | None,
#     zip_code: str | None,
#     facility_type: str | None,
#     facility_type_category: str | None,
#     page: int,
#     page_size: int,
# ) -> dict[str, Any]:
#     """
#     Assemble the full TypeSense request. Pure — no I/O, so it is unit-testable.
#     """
#     query, query_by, infix = build_query(q=q, name=name, city=city)
#     has_text_query = query != "*"

#     params: dict[str, Any] = {
#         "q": query,
#         "query_by": query_by,
#         "query_by_weights": ",".join(str(w) for w in QUERY_BY_WEIGHTS)
#         if query_by == ",".join(QUERY_BY_FIELDS)
#         else None,
#         "infix": infix,
#         "sort_by": build_sort_by(has_text_query),
#         "page": page,
#         "per_page": page_size,
#         # Prefix matching on the last token: "Sunri" matches "Sunrise" while
#         # the user is still typing.
#         "prefix": True,
#         "num_typos": NUM_TYPOS,
#         # Multi-word queries where no document contains every word: drop the
#         # least significant token and retry rather than returning nothing.
#         # "sunrise senior living napa" should still find "Sunrise of Napa".
#         "drop_tokens_threshold": 1,
#         # Only fetch what the response actually uses. Cuts payload size and
#         # keeps the index's other fields from leaking into the API.
#         "include_fields": (
#             "id,name,facility_type,facility_type_category,city,state,zip_code,"
#             "latitude,longitude,overall_rating,bed_count,ownership_type"
#         ),
#         # An exact match should outrank a fuzzy one even if the fuzzy match
#         # scores higher on other signals.
#         "prioritize_exact_match": True,
#     }

#     filter_by = build_filter_by(
#         state=state,
#         zip_code=zip_code,
#         facility_type=facility_type,
#         facility_type_category=facility_type_category,
#     )
#     if filter_by:
#         params["filter_by"] = filter_by

#     # TypeSense rejects unknown/None values — strip empties rather than
#     # conditionally building the dict above, which would hurt readability.
#     return {key: val for key, val in params.items() if val is not None}


# def _to_card_dict(hit: dict[str, Any]) -> dict[str, Any]:
#     """
#     One TypeSense hit -> the exact key set `FacilityCard` expects.

#     Missing keys become None explicitly. TypeSense omits absent optional
#     fields, but `FacilityCard` declares them as `Optional[...] = None`, and
#     being explicit here means a schema change surfaces as a validation error
#     instead of a silently missing attribute.
#     """
#     doc = hit.get("document", {})
#     return {
#         "id": doc.get("id"),
#         "name": doc.get("name"),
#         "facility_type": doc.get("facility_type"),
#         "facility_type_category": doc.get("facility_type_category"),
#         "city": doc.get("city"),
#         "state": doc.get("state"),
#         "zip_code": doc.get("zip_code"),
#         "latitude": doc.get("latitude"),
#         "longitude": doc.get("longitude"),
#         "overall_rating": doc.get("overall_rating"),
#         "bed_count": doc.get("bed_count"),
#         "ownership_type": doc.get("ownership_type"),
#     }


# async def search_facilities(
#     *,
#     q: str | None = None,
#     name: str | None = None,
#     city: str | None = None,
#     state: str | None = None,
#     zip_code: str | None = None,
#     facility_type: str | None = None,
#     facility_type_category: str | None = None,
#     page: int = 1,
#     page_size: int = 20,
#     collection_name: str | None = None,
# ) -> dict[str, Any]:
#     """
#     Run the search.

#     Returns a dict matching `PaginatedFacilities`:
#         {items: [...], page, page_size, total, has_more}

#     Raises:
#         TypesenseUnavailable / ObjectNotFound — propagated so the endpoint can
#         fall back to Postgres. This function never swallows them, because a
#         silent fallback that nobody can see is how a "working" search quietly
#         stops using the search engine it was migrated to.
#     """
#     collection = collection_name or settings.TYPESENSE_COLLECTION

#     # Guard the result window before we spend a network round trip on a
#     # request TypeSense will reject outright.
#     if page * page_size > MAX_RESULT_WINDOW:
#         logger.info(
#             "Result window exceeded | page=%s page_size=%s max=%s — returning empty page",
#             page,
#             page_size,
#             MAX_RESULT_WINDOW,
#         )
#         return {
#             "items": [],
#             "page": page,
#             "page_size": page_size,
#             "total": MAX_RESULT_WINDOW,
#             "has_more": False,
#         }

#     params = build_search_params(
#         q=q,
#         name=name,
#         city=city,
#         state=state,
#         zip_code=zip_code,
#         facility_type=facility_type,
#         facility_type_category=facility_type_category,
#         page=page,
#         page_size=page_size,
#     )

#     client = get_typesense_client()
#     result = await run_typesense(
#         client.collections[collection].documents.search, params
#     )

#     total = int(result.get("found", 0))
#     items = [_to_card_dict(hit) for hit in result.get("hits", [])]

#     logger.info(
#         "TypeSense search | q=%r filters=%r page=%s found=%s returned=%s took=%sms",
#         params.get("q"),
#         params.get("filter_by", ""),
#         page,
#         total,
#         len(items),
#         result.get("search_time_ms"),
#     )

#     return {
#         "items": items,
#         "page": page,
#         "page_size": page_size,
#         "total": total,
#         "has_more": (page * page_size) < total,
#     }


# async def suggest_facilities(
#     q: str,
#     limit: int = 8,
#     collection_name: str | None = None,
# ) -> list[dict[str, Any]]:
#     """
#     Autocomplete for the search box. Same index, tighter response.

#     Deliberately name-only with a single allowed typo: an autocomplete list
#     that "helpfully" widens the match is noise. The user is mid-word, and the
#     right behaviour is a short, obviously-relevant list.
#     """
#     collection = collection_name or settings.TYPESENSE_COLLECTION
#     client = get_typesense_client()

#     result = await run_typesense(
#         client.collections[collection].documents.search,
#         {
#             "q": q.strip() or "*",
#             "query_by": "name",
#             "infix": "fallback",
#             "prefix": True,
#             "num_typos": 1,
#             "per_page": max(1, min(limit, 50)),
#             "include_fields": "id,name,city,state",
#             "sort_by": "_text_match:desc,rating_sort:desc",
#         },
#     )

#     return [
#         {
#             "id": hit["document"].get("id"),
#             "name": hit["document"].get("name"),
#             "city": hit["document"].get("city"),
#             "state": hit["document"].get("state"),
#         }
#         for hit in result.get("hits", [])
#     ]
























# """
# TypeSense search execution for the facilities index.

# Owns exactly one thing: turning the existing `/facilities/search` query
# parameters into a TypeSense request and turning the response back into the
# shape the API already returns. It does NOT own the HTTP endpoint, the fallback
# decision, or caching — those stay in the endpoint so the Postgres path remains
# readable and reviewable side by side.

# CONTRACT PRESERVATION
# ---------------------
# The endpoint's parameters and response schema do not change. What changes is
# matching quality:

# * Postgres today: `fuzzy_or_exact()` per column — substring OR pg_trgm
#   similarity. Each column is matched independently.
# * TypeSense: real typo tolerance (edit distance), prefix matching, infix
#   matching on name/city, and relevance ranking across fields.

# The practical difference is that TypeSense returns MORE results for a typo'd
# query and orders them better. Filters that were exact in Postgres (`state`,
# `zip_code`) stay exact here — a typo-tolerant state filter would be actively
# wrong, since "CA" and "GA" are one edit apart.
# """
# from __future__ import annotations

# import logging
# from typing import Any, Sequence

# from app.core.config import settings
# from app.core.typesense import get_typesense_client, run_typesense
# from app.services.typesense_collection_service import (
#     FIELD_NUM_TYPOS,
#     INFIX_FIELDS,
#     QUERY_BY_FIELDS,
#     QUERY_BY_WEIGHTS,
# )

# logger = logging.getLogger("app.typesense.search")

# # TypeSense refuses a result window beyond this (page * per_page). Deep paging
# # past it is meaningless for a search UI anyway — nobody browses to page 500 —
# # but it must not 500 the endpoint.
# MAX_RESULT_WINDOW = 10_000

# # Fallback edit distance for any field without an entry in FIELD_NUM_TYPOS.
# # 2 is TypeSense's own default and the closest behavioural match to the
# # existing pg_trgm `word_similarity > 0.4` threshold.
# DEFAULT_NUM_TYPOS = 2


# def _escape_filter_value(value: str) -> str:
#     """
#     Make a value safe inside a `filter_by` expression.

#     TypeSense's filter grammar treats `&&`, `||`, `(`, `)`, `,`, `:` and
#     backticks as syntax. A city called "Winston-Salem" is fine, but an
#     unescaped user string containing them changes the meaning of the filter —
#     the search-engine equivalent of SQL injection. Backtick-quoting the value
#     and stripping backticks from inside it removes the whole class.
#     """
#     return "`" + value.replace("`", "") + "`"


# def build_filter_by(
#     *,
#     state: str | None = None,
#     zip_code: str | None = None,
#     facility_type: str | None = None,
#     facility_type_category: str | None = None,
# ) -> str:
#     """
#     Structured, exact filters — the parts of the query that must not be fuzzy.

#     `state` is expected to be already normalized to a 2-letter abbreviation by
#     the caller (the endpoint runs the existing `normalize_state()` so
#     "California" keeps working). Values are upper-cased here to match how the
#     mapper stores them.
#     """
#     clauses: list[str] = []

#     if state:
#         clauses.append(f"state:={_escape_filter_value(state.strip().upper())}")
#     if zip_code:
#         clauses.append(f"zip_code:={_escape_filter_value(zip_code.strip())}")
#     if facility_type_category:
#         clauses.append(
#             f"facility_type_category:={_escape_filter_value(facility_type_category.strip())}"
#         )
#     if facility_type:
#         clauses.append(f"facility_type:={_escape_filter_value(facility_type.strip())}")

#     return " && ".join(clauses)


# def _field_params(fields: Sequence[str]) -> dict[str, str]:
#     """
#     Build the positional, comma-separated per-field strings TypeSense expects.

#     `query_by`, `query_by_weights`, `infix` and `num_typos` must each have
#     exactly one entry per searched field, in the same order. Hardcoding those
#     strings is how they drift: add one field to `query_by` and the four-entry
#     `infix` string silently becomes wrong — TypeSense rejects the request at
#     runtime, and only for the code path that uses that particular field
#     combination, so it is easy to miss in testing.

#     Deriving all four from one tuple makes that class of bug impossible.
#     """
#     weight_of = dict(zip(QUERY_BY_FIELDS, QUERY_BY_WEIGHTS))
#     return {
#         "query_by": ",".join(fields),
#         # Fields outside QUERY_BY_FIELDS have no declared weight; 1 is
#         # TypeSense's own default.
#         "query_by_weights": ",".join(str(weight_of.get(f, 1)) for f in fields),
#         "infix": ",".join("fallback" if f in INFIX_FIELDS else "off" for f in fields),
#         "num_typos": ",".join(str(FIELD_NUM_TYPOS.get(f, DEFAULT_NUM_TYPOS)) for f in fields),
#     }


# def build_query(
#     *,
#     q: str | None = None,
#     name: str | None = None,
#     city: str | None = None,
# ) -> tuple[str, list[str]]:
#     """
#     Decide the free-text query and which fields it runs against.

#     Returns:
#         (query_string, searched_fields)

#     The field scoping matters. `city="Napa"` searched across every field would
#     surface a facility on "Napa Street" in Los Angeles — a precision regression
#     versus the current Postgres behaviour, which only ever looks at the city
#     column. So each parameter keeps its own target fields:

#         name only  -> ["name"]
#         city only  -> ["city"]
#         both       -> ["name", "city"]
#         q (new)    -> every field in QUERY_BY_FIELDS, weighted

#     `q` is an ADDITIVE optional parameter. Nothing sends it today, so nothing
#     changes. It exists because the mobile client currently issues a second
#     request when a one-word search comes back empty (guess city, retry as
#     name) — see `searchFacilitiesSmart` in the app. TypeSense can OR across
#     fields in a single request, so once the client adopts `q` that retry and
#     its extra round trip disappear.

#     With `zip_code` in QUERY_BY_FIELDS, a single `q` box now handles every way
#     a user might search: facility name, city, state, street address or ZIP.
#     """
#     if q and q.strip():
#         return q.strip(), list(QUERY_BY_FIELDS)

#     terms: list[str] = []
#     fields: list[str] = []

#     if name and name.strip():
#         terms.append(name.strip())
#         fields.append("name")
#     if city and city.strip():
#         terms.append(city.strip())
#         fields.append("city")

#     if not terms:
#         # TypeSense's match-all. Used when the request is filters-only
#         # (e.g. "everything in CA"), which the browse screen does on load.
#         return "*", ["name"]

#     return " ".join(terms), fields


# def build_sort_by(has_text_query: bool) -> str:
#     """
#     Ranking.

#     With a text query, relevance leads — a name match must beat a
#     higher-rated facility that only matched on address. `rating_sort` then
#     breaks relevance ties, and `name` breaks the remaining ones so pagination
#     is deterministic (without a total order, the same document can appear on
#     two pages).

#     Without a text query this reproduces the existing Postgres ordering
#     exactly: `ORDER BY overall_rating DESC NULLS LAST, name`. `rating_sort` is
#     0.0 for unrated facilities, which is what puts them last.
#     """
#     if has_text_query:
#         return "_text_match:desc,rating_sort:desc,name:asc"
#     return "rating_sort:desc,name:asc"


# def build_search_params(
#     *,
#     q: str | None,
#     name: str | None,
#     city: str | None,
#     state: str | None,
#     zip_code: str | None,
#     facility_type: str | None,
#     facility_type_category: str | None,
#     page: int,
#     page_size: int,
# ) -> dict[str, Any]:
#     """
#     Assemble the full TypeSense request. Pure — no I/O, so it is unit-testable.
#     """
#     query, fields = build_query(q=q, name=name, city=city)
#     has_text_query = query != "*"

#     params: dict[str, Any] = {
#         "q": query,
#         **_field_params(fields),
#         "sort_by": build_sort_by(has_text_query),
#         "page": page,
#         "per_page": page_size,
#         # Prefix matching on the last token: "Sunri" matches "Sunrise" while
#         # the user is still typing — and "945" narrows to matching ZIPs even
#         # though ZIP typo tolerance is 0.
#         "prefix": True,
#         # Multi-word queries where no document contains every word: drop the
#         # least significant token and retry rather than returning nothing.
#         # "sunrise senior living napa" should still find "Sunrise of Napa".
#         "drop_tokens_threshold": 1,
#         # Only fetch what the response actually uses. Cuts payload size and
#         # keeps the index's other fields from leaking into the API.
#         "include_fields": (
#             "id,name,facility_type,facility_type_category,city,state,zip_code,"
#             "latitude,longitude,overall_rating,bed_count,ownership_type"
#         ),
#         # An exact match should outrank a fuzzy one even if the fuzzy match
#         # scores higher on other signals.
#         "prioritize_exact_match": True,
#     }

#     filter_by = build_filter_by(
#         state=state,
#         zip_code=zip_code,
#         facility_type=facility_type,
#         facility_type_category=facility_type_category,
#     )
#     if filter_by:
#         params["filter_by"] = filter_by

#     # TypeSense rejects unknown/None values — strip empties rather than
#     # conditionally building the dict above, which would hurt readability.
#     return {key: val for key, val in params.items() if val is not None}


# def _to_card_dict(hit: dict[str, Any]) -> dict[str, Any]:
#     """
#     One TypeSense hit -> the exact key set `FacilityCard` expects.

#     Missing keys become None explicitly. TypeSense omits absent optional
#     fields, but `FacilityCard` declares them as `Optional[...] = None`, and
#     being explicit here means a schema change surfaces as a validation error
#     instead of a silently missing attribute.
#     """
#     doc = hit.get("document", {})
#     return {
#         "id": doc.get("id"),
#         "name": doc.get("name"),
#         "facility_type": doc.get("facility_type"),
#         "facility_type_category": doc.get("facility_type_category"),
#         "city": doc.get("city"),
#         "state": doc.get("state"),
#         "zip_code": doc.get("zip_code"),
#         "latitude": doc.get("latitude"),
#         "longitude": doc.get("longitude"),
#         "overall_rating": doc.get("overall_rating"),
#         "bed_count": doc.get("bed_count"),
#         "ownership_type": doc.get("ownership_type"),
#     }


# async def search_facilities(
#     *,
#     q: str | None = None,
#     name: str | None = None,
#     city: str | None = None,
#     state: str | None = None,
#     zip_code: str | None = None,
#     facility_type: str | None = None,
#     facility_type_category: str | None = None,
#     page: int = 1,
#     page_size: int = 20,
#     collection_name: str | None = None,
# ) -> dict[str, Any]:
#     """
#     Run the search.

#     Returns a dict matching `PaginatedFacilities`:
#         {items: [...], page, page_size, total, has_more}

#     Raises:
#         TypesenseUnavailable / ObjectNotFound — propagated so the endpoint can
#         fall back to Postgres. This function never swallows them, because a
#         silent fallback that nobody can see is how a "working" search quietly
#         stops using the search engine it was migrated to.
#     """
#     collection = collection_name or settings.TYPESENSE_COLLECTION

#     # Guard the result window before we spend a network round trip on a
#     # request TypeSense will reject outright.
#     if page * page_size > MAX_RESULT_WINDOW:
#         logger.info(
#             "Result window exceeded | page=%s page_size=%s max=%s — returning empty page",
#             page,
#             page_size,
#             MAX_RESULT_WINDOW,
#         )
#         return {
#             "items": [],
#             "page": page,
#             "page_size": page_size,
#             "total": MAX_RESULT_WINDOW,
#             "has_more": False,
#         }

#     params = build_search_params(
#         q=q,
#         name=name,
#         city=city,
#         state=state,
#         zip_code=zip_code,
#         facility_type=facility_type,
#         facility_type_category=facility_type_category,
#         page=page,
#         page_size=page_size,
#     )

#     client = get_typesense_client()
#     result = await run_typesense(
#         client.collections[collection].documents.search, params
#     )

#     total = int(result.get("found", 0))
#     items = [_to_card_dict(hit) for hit in result.get("hits", [])]

#     logger.info(
#         "TypeSense search | q=%r filters=%r page=%s found=%s returned=%s took=%sms",
#         params.get("q"),
#         params.get("filter_by", ""),
#         page,
#         total,
#         len(items),
#         result.get("search_time_ms"),
#     )

#     return {
#         "items": items,
#         "page": page,
#         "page_size": page_size,
#         "total": total,
#         "has_more": (page * page_size) < total,
#     }


# async def suggest_facilities(
#     q: str,
#     limit: int = 8,
#     collection_name: str | None = None,
# ) -> list[dict[str, Any]]:
#     """
#     Autocomplete for the search box. Same index, tighter response.

#     Deliberately name-only with a single allowed typo: an autocomplete list
#     that "helpfully" widens the match is noise. The user is mid-word, and the
#     right behaviour is a short, obviously-relevant list.
#     """
#     collection = collection_name or settings.TYPESENSE_COLLECTION
#     client = get_typesense_client()

#     result = await run_typesense(
#         client.collections[collection].documents.search,
#         {
#             "q": q.strip() or "*",
#             **_field_params(["name"]),
#             # Override the field default: autocomplete fires on every keystroke,
#             # and a second allowed typo on a half-typed word produces noise
#             # rather than help.
#             "num_typos": "1",
#             "prefix": True,
#             "per_page": max(1, min(limit, 50)),
#             "include_fields": "id,name,city,state",
#             "sort_by": "_text_match:desc,rating_sort:desc",
#         },
#     )

#     return [
#         {
#             "id": hit["document"].get("id"),
#             "name": hit["document"].get("name"),
#             "city": hit["document"].get("city"),
#             "state": hit["document"].get("state"),
#         }
#         for hit in result.get("hits", [])
#     ]






















# """
# TypeSense search execution for the facilities index.

# Owns exactly one thing: turning the existing `/facilities/search` query
# parameters into a TypeSense request and turning the response back into the
# shape the API already returns. It does NOT own the HTTP endpoint, the fallback
# decision, or caching — those stay in the endpoint so the Postgres path remains
# readable and reviewable side by side.

# CONTRACT PRESERVATION
# ---------------------
# The endpoint's parameters and response schema do not change. What changes is
# matching quality:

# * Postgres today: `fuzzy_or_exact()` per column — substring OR pg_trgm
#   similarity. Each column is matched independently.
# * TypeSense: real typo tolerance (edit distance), prefix matching, infix
#   matching on name/city, and relevance ranking across fields.

# The practical difference is that TypeSense returns MORE results for a typo'd
# query and orders them better. Filters that were exact in Postgres (`state`,
# `zip_code`) stay exact here — a typo-tolerant state filter would be actively
# wrong, since "CA" and "GA" are one edit apart.
# """
# from __future__ import annotations

# import logging
# from typing import Any, Sequence

# from app.core.config import settings
# from app.core.typesense import get_typesense_client, run_typesense
# from app.services.typesense_collection_service import (
#     FIELD_NUM_TYPOS,
#     INFIX_FIELDS,
#     QUERY_BY_FIELDS,
#     QUERY_BY_WEIGHTS,
# )

# logger = logging.getLogger("app.typesense.search")

# # TypeSense refuses a result window beyond this (page * per_page). Deep paging
# # past it is meaningless for a search UI anyway — nobody browses to page 500 —
# # but it must not 500 the endpoint.
# MAX_RESULT_WINDOW = 10_000

# # Fallback edit distance for any field without an entry in FIELD_NUM_TYPOS.
# # 2 is TypeSense's own default and the closest behavioural match to the
# # existing pg_trgm `word_similarity > 0.4` threshold.
# DEFAULT_NUM_TYPOS = 2


# def _escape_filter_value(value: str) -> str:
#     """
#     Make a value safe inside a `filter_by` expression.

#     TypeSense's filter grammar treats `&&`, `||`, `(`, `)`, `,`, `:` and
#     backticks as syntax. A city called "Winston-Salem" is fine, but an
#     unescaped user string containing them changes the meaning of the filter —
#     the search-engine equivalent of SQL injection. Backtick-quoting the value
#     and stripping backticks from inside it removes the whole class.
#     """
#     return "`" + value.replace("`", "") + "`"


# def build_filter_by(
#     *,
#     state: str | None = None,
#     zip_code: str | None = None,
#     facility_type: str | None = None,
#     facility_type_category: str | None = None,
# ) -> str:
#     """
#     Structured, exact filters — the parts of the query that must not be fuzzy.

#     `state` is expected to be already normalized to a 2-letter abbreviation by
#     the caller (the endpoint runs the existing `normalize_state()` so
#     "California" keeps working). Values are upper-cased here to match how the
#     mapper stores them.
#     """
#     clauses: list[str] = []

#     if state:
#         clauses.append(f"state:={_escape_filter_value(state.strip().upper())}")
#     if zip_code:
#         clauses.append(f"zip_code:={_escape_filter_value(zip_code.strip())}")
#     if facility_type_category:
#         clauses.append(
#             f"facility_type_category:={_escape_filter_value(facility_type_category.strip())}"
#         )
#     if facility_type:
#         clauses.append(f"facility_type:={_escape_filter_value(facility_type.strip())}")

#     return " && ".join(clauses)


# def _field_params(fields: Sequence[str]) -> dict[str, str]:
#     """
#     Build the positional, comma-separated per-field strings TypeSense expects.

#     `query_by`, `query_by_weights`, `infix` and `num_typos` must each have
#     exactly one entry per searched field, in the same order. Hardcoding those
#     strings is how they drift: add one field to `query_by` and the four-entry
#     `infix` string silently becomes wrong — TypeSense rejects the request at
#     runtime, and only for the code path that uses that particular field
#     combination, so it is easy to miss in testing.

#     Deriving all four from one tuple makes that class of bug impossible.
#     """
#     weight_of = dict(zip(QUERY_BY_FIELDS, QUERY_BY_WEIGHTS))
#     return {
#         "query_by": ",".join(fields),
#         # Fields outside QUERY_BY_FIELDS have no declared weight; 1 is
#         # TypeSense's own default.
#         "query_by_weights": ",".join(str(weight_of.get(f, 1)) for f in fields),
#         "infix": ",".join("fallback" if f in INFIX_FIELDS else "off" for f in fields),
#         "num_typos": ",".join(str(FIELD_NUM_TYPOS.get(f, DEFAULT_NUM_TYPOS)) for f in fields),
#     }


# def build_query(
#     *,
#     q: str | None = None,
#     name: str | None = None,
#     city: str | None = None,
# ) -> tuple[str, list[str]]:
#     """
#     Decide the free-text query and which fields it runs against.

#     Returns:
#         (query_string, searched_fields)

#     The field scoping matters. `city="Napa"` searched across every field would
#     surface a facility on "Napa Street" in Los Angeles — a precision regression
#     versus the current Postgres behaviour, which only ever looks at the city
#     column. So each parameter keeps its own target fields:

#         name only  -> ["name"]
#         city only  -> ["city"]
#         both       -> ["name", "city"]
#         q (new)    -> every field in QUERY_BY_FIELDS, weighted

#     `q` is an ADDITIVE optional parameter. Nothing sends it today, so nothing
#     changes. It exists because the mobile client currently issues a second
#     request when a one-word search comes back empty (guess city, retry as
#     name) — see `searchFacilitiesSmart` in the app. TypeSense can OR across
#     fields in a single request, so once the client adopts `q` that retry and
#     its extra round trip disappear.

#     With `zip_code` in QUERY_BY_FIELDS, a single `q` box now handles every way
#     a user might search: facility name, city, state, street address or ZIP.
#     """
#     if q and q.strip():
#         return q.strip(), list(QUERY_BY_FIELDS)

#     terms: list[str] = []
#     fields: list[str] = []

#     if name and name.strip():
#         terms.append(name.strip())
#         fields.append("name")
#     if city and city.strip():
#         terms.append(city.strip())
#         fields.append("city")

#     if not terms:
#         # TypeSense's match-all. Used when the request is filters-only
#         # (e.g. "everything in CA"), which the browse screen does on load.
#         return "*", ["name"]

#     return " ".join(terms), fields


# def build_sort_by(has_text_query: bool) -> str:
#     """
#     Ranking.

#     With a text query, relevance leads — a name match must beat a
#     higher-rated facility that only matched on address. `rating_sort` then
#     breaks relevance ties, and `name` breaks the remaining ones so pagination
#     is deterministic (without a total order, the same document can appear on
#     two pages).

#     Without a text query this reproduces the existing Postgres ordering
#     exactly: `ORDER BY overall_rating DESC NULLS LAST, name`. `rating_sort` is
#     0.0 for unrated facilities, which is what puts them last.
#     """
#     if has_text_query:
#         return "_text_match:desc,rating_sort:desc,name:asc"
#     return "rating_sort:desc,name:asc"


# def build_search_params(
#     *,
#     q: str | None,
#     name: str | None,
#     city: str | None,
#     state: str | None,
#     zip_code: str | None,
#     facility_type: str | None,
#     facility_type_category: str | None,
#     page: int,
#     page_size: int,
# ) -> dict[str, Any]:
#     """
#     Assemble the full TypeSense request. Pure — no I/O, so it is unit-testable.
#     """
#     query, fields = build_query(q=q, name=name, city=city)
#     has_text_query = query != "*"

#     params: dict[str, Any] = {
#         "q": query,
#         **_field_params(fields),
#         "sort_by": build_sort_by(has_text_query),
#         "page": page,
#         "per_page": page_size,
#         # Prefix matching on the last token: "Sunri" matches "Sunrise" while
#         # the user is still typing — and "945" narrows to matching ZIPs even
#         # though ZIP typo tolerance is 0.
#         "prefix": True,
#         # Multi-word queries where no document contains every word: drop the
#         # least significant token and retry rather than returning nothing.
#         # "sunrise senior living napa" should still find "Sunrise of Napa".
#         "drop_tokens_threshold": 1,
#         # Only fetch what the response actually uses. Cuts payload size and
#         # keeps the index's other fields from leaking into the API.
#         "include_fields": (
#             "id,name,facility_type,facility_type_category,city,state,zip_code,"
#             "latitude,longitude,overall_rating,bed_count,ownership_type"
#         ),
#         # An exact match should outrank a fuzzy one even if the fuzzy match
#         # scores higher on other signals.
#         "prioritize_exact_match": True,
#     }

#     filter_by = build_filter_by(
#         state=state,
#         zip_code=zip_code,
#         facility_type=facility_type,
#         facility_type_category=facility_type_category,
#     )
#     if filter_by:
#         params["filter_by"] = filter_by

#     # TypeSense rejects unknown/None values — strip empties rather than
#     # conditionally building the dict above, which would hurt readability.
#     return {key: val for key, val in params.items() if val is not None}


# def _to_card_dict(hit: dict[str, Any]) -> dict[str, Any]:
#     """
#     One TypeSense hit -> the exact key set `FacilityCard` expects.

#     Missing keys become None explicitly. TypeSense omits absent optional
#     fields, but `FacilityCard` declares them as `Optional[...] = None`, and
#     being explicit here means a schema change surfaces as a validation error
#     instead of a silently missing attribute.
#     """
#     doc = hit.get("document", {})
#     return {
#         "id": doc.get("id"),
#         "name": doc.get("name"),
#         "facility_type": doc.get("facility_type"),
#         "facility_type_category": doc.get("facility_type_category"),
#         "city": doc.get("city"),
#         "state": doc.get("state"),
#         "zip_code": doc.get("zip_code"),
#         "latitude": doc.get("latitude"),
#         "longitude": doc.get("longitude"),
#         "overall_rating": doc.get("overall_rating"),
#         "bed_count": doc.get("bed_count"),
#         "ownership_type": doc.get("ownership_type"),
#     }


# async def search_facilities(
#     *,
#     q: str | None = None,
#     name: str | None = None,
#     city: str | None = None,
#     state: str | None = None,
#     zip_code: str | None = None,
#     facility_type: str | None = None,
#     facility_type_category: str | None = None,
#     page: int = 1,
#     page_size: int = 20,
#     collection_name: str | None = None,
# ) -> dict[str, Any]:
#     """
#     Run the search.

#     Returns a dict matching `PaginatedFacilities`:
#         {items: [...], page, page_size, total, has_more}

#     Raises:
#         TypesenseUnavailable / ObjectNotFound — propagated so the endpoint can
#         fall back to Postgres. This function never swallows them, because a
#         silent fallback that nobody can see is how a "working" search quietly
#         stops using the search engine it was migrated to.
#     """
#     collection = collection_name or settings.TYPESENSE_COLLECTION

#     # Guard the result window before we spend a network round trip on a
#     # request TypeSense will reject outright.
#     if page * page_size > MAX_RESULT_WINDOW:
#         logger.info(
#             "Result window exceeded | page=%s page_size=%s max=%s — returning empty page",
#             page,
#             page_size,
#             MAX_RESULT_WINDOW,
#         )
#         return {
#             "items": [],
#             "page": page,
#             "page_size": page_size,
#             "total": MAX_RESULT_WINDOW,
#             "has_more": False,
#         }

#     params = build_search_params(
#         q=q,
#         name=name,
#         city=city,
#         state=state,
#         zip_code=zip_code,
#         facility_type=facility_type,
#         facility_type_category=facility_type_category,
#         page=page,
#         page_size=page_size,
#     )

#     client = get_typesense_client()
#     result = await run_typesense(
#         client.collections[collection].documents.search, params
#     )

#     total = int(result.get("found", 0))
#     items = [_to_card_dict(hit) for hit in result.get("hits", [])]

#     logger.info(
#         "TypeSense search | q=%r filters=%r page=%s found=%s returned=%s took=%sms",
#         params.get("q"),
#         params.get("filter_by", ""),
#         page,
#         total,
#         len(items),
#         result.get("search_time_ms"),
#     )

#     return {
#         "items": items,
#         "page": page,
#         "page_size": page_size,
#         "total": total,
#         "has_more": (page * page_size) < total,
#     }


# async def suggest_facilities(
#     q: str,
#     limit: int = 8,
#     collection_name: str | None = None,
# ) -> list[dict[str, Any]]:
#     """
#     Autocomplete for the search box. Same index, tighter response.

#     Searches the SAME fields as the full search (name, city, address, state,
#     ZIP) so the dropdown answers every way a user types: a facility name, a
#     city, a street address or a ZIP code. It was previously name-only, which
#     is why typing a ZIP or a city produced no suggestions at all.

#     Matching stays precise, not "helpfully" wide, because it inherits the
#     per-field typo budget from FIELD_NUM_TYPOS via `_field_params`: name/city
#     tolerate typos, but `state` and `zip_code` are exact (num_typos 0) - a
#     fuzzy ZIP or state would turn autocomplete into noise ("94558" must not
#     also surface "94559"). `prefix=True` is what makes it feel live while the
#     user is still mid-word.
#     """
#     collection = collection_name or settings.TYPESENSE_COLLECTION
#     client = get_typesense_client()

#     result = await run_typesense(
#         client.collections[collection].documents.search,
#         {
#             "q": q.strip() or "*",
#             # All searchable fields, with the shared weights / infix / per-field
#             # typo budget. No hardcoded num_typos override, so state and ZIP
#             # stay exact (0) instead of being forced to a single blanket value.
#             **_field_params(list(QUERY_BY_FIELDS)),
#             "prefix": True,
#             "drop_tokens_threshold": 1,
#             "prioritize_exact_match": True,
#             "per_page": max(1, min(limit, 50)),
#             # address + zip added so the dropdown can show a precise second
#             # line (street / city, state ZIP) and disambiguate same-named rows.
#             "include_fields": "id,name,address,city,state,zip_code",
#             "sort_by": "_text_match:desc,rating_sort:desc",
#         },
#     )

#     return [
#         {
#             "id": hit["document"].get("id"),
#             "name": hit["document"].get("name"),
#             "address": hit["document"].get("address"),
#             "city": hit["document"].get("city"),
#             "state": hit["document"].get("state"),
#             "zip_code": hit["document"].get("zip_code"),
#         }
#         for hit in result.get("hits", [])
#     ]















"""
TypeSense search execution for the facilities index.

Owns exactly one thing: turning the existing `/facilities/search` query
parameters into a TypeSense request and turning the response back into the
shape the API already returns. It does NOT own the HTTP endpoint, the fallback
decision, or caching — those stay in the endpoint so the Postgres path remains
readable and reviewable side by side.

CONTRACT PRESERVATION
---------------------
The endpoint's parameters and response schema do not change. What changes is
matching quality:

* Postgres today: `fuzzy_or_exact()` per column — substring OR pg_trgm
  similarity. Each column is matched independently.
* TypeSense: real typo tolerance (edit distance), prefix matching, infix
  matching on name/city, and relevance ranking across fields.

The practical difference is that TypeSense returns MORE results for a typo'd
query and orders them better. Filters that were exact in Postgres (`state`,
`zip_code`) stay exact here — a typo-tolerant state filter would be actively
wrong, since "CA" and "GA" are one edit apart.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from app.core.config import settings
from app.core.typesense import get_typesense_client, run_typesense
from app.services.typesense_collection_service import (
    FIELD_NUM_TYPOS,
    INFIX_FIELDS,
    QUERY_BY_FIELDS,
    QUERY_BY_WEIGHTS,
)

logger = logging.getLogger("app.typesense.search")

# TypeSense refuses a result window beyond this (page * per_page). Deep paging
# past it is meaningless for a search UI anyway — nobody browses to page 500 —
# but it must not 500 the endpoint.
MAX_RESULT_WINDOW = 10_000

# Fallback edit distance for any field without an entry in FIELD_NUM_TYPOS.
# 2 is TypeSense's own default and the closest behavioural match to the
# existing pg_trgm `word_similarity > 0.4` threshold.
DEFAULT_NUM_TYPOS = 2


def _escape_filter_value(value: str) -> str:
    """
    Make a value safe inside a `filter_by` expression.

    TypeSense's filter grammar treats `&&`, `||`, `(`, `)`, `,`, `:` and
    backticks as syntax. A city called "Winston-Salem" is fine, but an
    unescaped user string containing them changes the meaning of the filter —
    the search-engine equivalent of SQL injection. Backtick-quoting the value
    and stripping backticks from inside it removes the whole class.
    """
    return "`" + value.replace("`", "") + "`"


def build_filter_by(
    *,
    state: str | None = None,
    zip_code: str | None = None,
    facility_type: str | None = None,
    facility_type_category: str | None = None,
) -> str:
    """
    Structured, exact filters — the parts of the query that must not be fuzzy.

    `state` is expected to be already normalized to a 2-letter abbreviation by
    the caller (the endpoint runs the existing `normalize_state()` so
    "California" keeps working). Values are upper-cased here to match how the
    mapper stores them.
    """
    clauses: list[str] = []

    if state:
        clauses.append(f"state:={_escape_filter_value(state.strip().upper())}")
    if zip_code:
        clauses.append(f"zip_code:={_escape_filter_value(zip_code.strip())}")
    if facility_type_category:
        clauses.append(
            f"facility_type_category:={_escape_filter_value(facility_type_category.strip())}"
        )
    if facility_type:
        clauses.append(f"facility_type:={_escape_filter_value(facility_type.strip())}")

    return " && ".join(clauses)


def _field_params(fields: Sequence[str]) -> dict[str, str]:
    """
    Build the positional, comma-separated per-field strings TypeSense expects.

    `query_by`, `query_by_weights`, `infix` and `num_typos` must each have
    exactly one entry per searched field, in the same order. Hardcoding those
    strings is how they drift: add one field to `query_by` and the four-entry
    `infix` string silently becomes wrong — TypeSense rejects the request at
    runtime, and only for the code path that uses that particular field
    combination, so it is easy to miss in testing.

    Deriving all four from one tuple makes that class of bug impossible.
    """
    weight_of = dict(zip(QUERY_BY_FIELDS, QUERY_BY_WEIGHTS))
    return {
        "query_by": ",".join(fields),
        # Fields outside QUERY_BY_FIELDS have no declared weight; 1 is
        # TypeSense's own default.
        "query_by_weights": ",".join(str(weight_of.get(f, 1)) for f in fields),
        "infix": ",".join("fallback" if f in INFIX_FIELDS else "off" for f in fields),
        "num_typos": ",".join(str(FIELD_NUM_TYPOS.get(f, DEFAULT_NUM_TYPOS)) for f in fields),
    }


def build_query(
    *,
    q: str | None = None,
    name: str | None = None,
    city: str | None = None,
) -> tuple[str, list[str]]:
    """
    Decide the free-text query and which fields it runs against.

    Returns:
        (query_string, searched_fields)

    The field scoping matters. `city="Napa"` searched across every field would
    surface a facility on "Napa Street" in Los Angeles — a precision regression
    versus the current Postgres behaviour, which only ever looks at the city
    column. So each parameter keeps its own target fields:

        name only  -> ["name"]
        city only  -> ["city"]
        both       -> ["name", "city"]
        q (new)    -> every field in QUERY_BY_FIELDS, weighted

    `q` is an ADDITIVE optional parameter. Nothing sends it today, so nothing
    changes. It exists because the mobile client currently issues a second
    request when a one-word search comes back empty (guess city, retry as
    name) — see `searchFacilitiesSmart` in the app. TypeSense can OR across
    fields in a single request, so once the client adopts `q` that retry and
    its extra round trip disappear.

    With `zip_code` in QUERY_BY_FIELDS, a single `q` box now handles every way
    a user might search: facility name, city, state, street address or ZIP.
    """
    if q and q.strip():
        return q.strip(), list(QUERY_BY_FIELDS)

    terms: list[str] = []
    fields: list[str] = []

    if name and name.strip():
        terms.append(name.strip())
        fields.append("name")
    if city and city.strip():
        terms.append(city.strip())
        fields.append("city")

    if not terms:
        # TypeSense's match-all. Used when the request is filters-only
        # (e.g. "everything in CA"), which the browse screen does on load.
        return "*", ["name"]

    return " ".join(terms), fields


def build_sort_by(has_text_query: bool) -> str:
    """
    Ranking.

    With a text query, relevance leads — a name match must beat a
    higher-rated facility that only matched on address. `rating_sort` then
    breaks relevance ties, and `name` breaks the remaining ones so pagination
    is deterministic (without a total order, the same document can appear on
    two pages).

    Without a text query this reproduces the existing Postgres ordering
    exactly: `ORDER BY overall_rating DESC NULLS LAST, name`. `rating_sort` is
    0.0 for unrated facilities, which is what puts them last.
    """
    if has_text_query:
        return "_text_match:desc,rating_sort:desc,name:asc"
    return "rating_sort:desc,name:asc"


def build_search_params(
    *,
    q: str | None,
    name: str | None,
    city: str | None,
    state: str | None,
    zip_code: str | None,
    facility_type: str | None,
    facility_type_category: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """
    Assemble the full TypeSense request. Pure — no I/O, so it is unit-testable.
    """
    query, fields = build_query(q=q, name=name, city=city)
    has_text_query = query != "*"

    params: dict[str, Any] = {
        "q": query,
        **_field_params(fields),
        "sort_by": build_sort_by(has_text_query),
        "page": page,
        "per_page": page_size,
        # Prefix matching on the last token: "Sunri" matches "Sunrise" while
        # the user is still typing — and "945" narrows to matching ZIPs even
        # though ZIP typo tolerance is 0.
        "prefix": True,
        # Multi-word queries where no document contains every word: drop the
        # least significant token and retry rather than returning nothing.
        # "sunrise senior living napa" should still find "Sunrise of Napa".
        "drop_tokens_threshold": 1,
        # Only fetch what the response actually uses. Cuts payload size and
        # keeps the index's other fields from leaking into the API.
        "include_fields": (
            "id,name,facility_type,facility_type_category,city,state,zip_code,"
            "latitude,longitude,overall_rating,bed_count,ownership_type"
        ),
        # An exact match should outrank a fuzzy one even if the fuzzy match
        # scores higher on other signals.
        "prioritize_exact_match": True,
    }

    filter_by = build_filter_by(
        state=state,
        zip_code=zip_code,
        facility_type=facility_type,
        facility_type_category=facility_type_category,
    )
    if filter_by:
        params["filter_by"] = filter_by

    # TypeSense rejects unknown/None values — strip empties rather than
    # conditionally building the dict above, which would hurt readability.
    return {key: val for key, val in params.items() if val is not None}


def _to_card_dict(hit: dict[str, Any]) -> dict[str, Any]:
    """
    One TypeSense hit -> the exact key set `FacilityCard` expects.

    Missing keys become None explicitly. TypeSense omits absent optional
    fields, but `FacilityCard` declares them as `Optional[...] = None`, and
    being explicit here means a schema change surfaces as a validation error
    instead of a silently missing attribute.
    """
    doc = hit.get("document", {})
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "facility_type": doc.get("facility_type"),
        "facility_type_category": doc.get("facility_type_category"),
        "city": doc.get("city"),
        "state": doc.get("state"),
        "zip_code": doc.get("zip_code"),
        "latitude": doc.get("latitude"),
        "longitude": doc.get("longitude"),
        "overall_rating": doc.get("overall_rating"),
        "bed_count": doc.get("bed_count"),
        "ownership_type": doc.get("ownership_type"),
    }


async def search_facilities(
    *,
    q: str | None = None,
    name: str | None = None,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    facility_type: str | None = None,
    facility_type_category: str | None = None,
    page: int = 1,
    page_size: int = 20,
    collection_name: str | None = None,
) -> dict[str, Any]:
    """
    Run the search.

    Returns a dict matching `PaginatedFacilities`:
        {items: [...], page, page_size, total, has_more}

    Raises:
        TypesenseUnavailable / ObjectNotFound — propagated so the endpoint can
        fall back to Postgres. This function never swallows them, because a
        silent fallback that nobody can see is how a "working" search quietly
        stops using the search engine it was migrated to.
    """
    collection = collection_name or settings.TYPESENSE_COLLECTION

    # Guard the result window before we spend a network round trip on a
    # request TypeSense will reject outright.
    if page * page_size > MAX_RESULT_WINDOW:
        logger.info(
            "Result window exceeded | page=%s page_size=%s max=%s — returning empty page",
            page,
            page_size,
            MAX_RESULT_WINDOW,
        )
        return {
            "items": [],
            "page": page,
            "page_size": page_size,
            "total": MAX_RESULT_WINDOW,
            "has_more": False,
        }

    params = build_search_params(
        q=q,
        name=name,
        city=city,
        state=state,
        zip_code=zip_code,
        facility_type=facility_type,
        facility_type_category=facility_type_category,
        page=page,
        page_size=page_size,
    )

    client = get_typesense_client()
    result = await run_typesense(
        client.collections[collection].documents.search, params
    )

    total = int(result.get("found", 0))
    items = [_to_card_dict(hit) for hit in result.get("hits", [])]

    logger.info(
        "TypeSense search | q=%r filters=%r page=%s found=%s returned=%s took=%sms",
        params.get("q"),
        params.get("filter_by", ""),
        page,
        total,
        len(items),
        result.get("search_time_ms"),
    )

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": (page * page_size) < total,
    }


# ZIP autocomplete is treated as a first-class case, separate from text: a run
# of digits is unambiguously a ZIP, so it should behave like a ZIP and nothing
# else — no name/city noise, no typo widening onto neighbouring ZIPs.
ZIP_EXACT_LEN = 5


async def suggest_facilities(
    q: str,
    limit: int = 8,
    collection_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    Autocomplete for the search box, with dedicated ZIP handling.

    Three modes, chosen from the shape of the input:

    * COMPLETE ZIP (5+ digits) -> EXACT filter on zip_code. Returns exactly
      that ZIP's facilities, or nothing at all. No fuzzy fallback: if the ZIP
      isn't in the data, an empty dropdown is the correct, honest answer —
      not a list of vaguely-similar ZIPs.

    * PARTIAL ZIP (1-4 digits) -> STRICT prefix on zip_code only. "95" returns
      only 95xxx and never 94xxx/93xxx. num_typos 0 plus BOTH token thresholds
      pinned to 0 stop TypeSense from dropping a digit or retrying with typos
      and quietly widening the match.

    * TEXT (anything with a non-digit) -> the broad match across name, city,
      address, state and ZIP, with the shared per-field typo budget
      (name/city fuzzy; state/zip exact) and prefix for a live feel.

    Response shape is identical in every mode, so the client needs no changes.
    """
    collection = collection_name or settings.TYPESENSE_COLLECTION
    client = get_typesense_client()

    term = q.strip()
    per_page = max(1, min(limit, 50))
    include = "id,name,address,city,state,zip_code"

    if term.isdigit():
        if len(term) >= ZIP_EXACT_LEN:
            # Complete ZIP -> exact-match filter. Backtick-escaped for the same
            # reason build_filter_by escapes: never let input alter the grammar.
            params: dict[str, Any] = {
                "q": "*",
                "query_by": "zip_code",
                "filter_by": f"zip_code:={_escape_filter_value(term[:ZIP_EXACT_LEN])}",
                "per_page": per_page,
                "include_fields": include,
                # zip_code is not a sortable field in the schema, so rank the
                # exact-ZIP set by rating (which IS the default sort field).
                "sort_by": "rating_sort:desc",
            }
        else:
            # Partial number -> strict ZIP prefix. Only 95xxx for "95".
            params = {
                "q": term,
                "query_by": "zip_code",
                "num_typos": "0",             # no fuzz: 95 must never reach 94
                "prefix": True,               # 95 -> 95xxx
                "drop_tokens_threshold": 0,   # never drop digits and widen
                "typo_tokens_threshold": 0,   # never retry the query with typos
                "per_page": per_page,
                "include_fields": include,
                "sort_by": "_text_match:desc,rating_sort:desc",
            }
    else:
        # Text query -> broad, precise-where-it-matters match across all fields.
        params = {
            "q": term or "*",
            **_field_params(list(QUERY_BY_FIELDS)),
            "prefix": True,
            "drop_tokens_threshold": 1,
            "prioritize_exact_match": True,
            "per_page": per_page,
            # address + zip so the dropdown can show a precise second line
            # (street / city, state ZIP) and disambiguate same-named rows.
            "include_fields": include,
            "sort_by": "_text_match:desc,rating_sort:desc",
        }

    result = await run_typesense(
        client.collections[collection].documents.search, params
    )

    return [
        {
            "id": hit["document"].get("id"),
            "name": hit["document"].get("name"),
            "address": hit["document"].get("address"),
            "city": hit["document"].get("city"),
            "state": hit["document"].get("state"),
            "zip_code": hit["document"].get("zip_code"),
        }
        for hit in result.get("hits", [])
    ]