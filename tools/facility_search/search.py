# """
# Phase 4 -- the query pipeline (architecture docs, Section 9), implemented as a
# single function an agent-facing StructuredTool calls directly (see
# tools/agent_tools.py's facility_search). Deliberately skips the docs' Stage 1
# ("Query Parsing") as a separate LLM call -- the calling agent already performs
# that extraction via this function's own arguments, since the actual entrypoint
# in this codebase is a tool-calling agent, not a standalone free-text endpoint.

# Stage numbers below reference architecture docs Section 9.2.

# Phase 5 change: search_facilities() now returns (text, cards) instead of a
# plain str. `text` is a short line for the LLM to build its reply around;
# `cards` is the structured facility data (or None when no search ran at all,
# e.g. a clarifying question) that the frontend renders as flashcards. The LLM
# never has to reproduce facility details in prose -- see agent_tools.py's
# content_and_artifact wiring.

# Phase 8 change: eval-driven testing showed the LLM was unreliable at judging
# "is this facility type covered, should I call google_search instead, what do
# I say about it" via prompt rules alone -- and a prompt fix for one case
# (EMERGENCY PROTOCOL precedence) measurably regressed an unrelated case,
# confirming the prompt was carrying decisions that belong in code. So
# search_facilities() now decides for itself: an unrecognized facility type or
# a genuinely empty search result triggers an internal web-search fallback
# (_cards_or_web_fallback), already worded with the required disclosure
# sentence and already tagged with the right card source -- the calling LLM
# just needs to call this one tool whenever the user wants a facility, full
# stop. Stage 7's low-confidence-*ranking* branch is deliberately NOT part of
# this -- that's a different situation (real matches exist, just not
# semantically confident), not a coverage/data-absence one.
# """
# import json

# from database import get_db_connection
# from logger import log_search, log_warn
# from tools.facility_search import fuzzy_match, qdrant_index
# from tools.facility_search.content_templates import TYPE_LABELS
# from tools.facility_search.embeddings import embed_texts, EMBEDDING_MODEL
# from tools.web_search import web_search

# from qdrant_client.models import Filter, FieldCondition, MatchValue

# RESULT_LIMIT = 5

# # One flat global constant -- a known, explicitly-flagged simplification, not a
# # settled design decision. The architecture docs' own Open Items (Section 11)
# # call for per-facility-type threshold calibration, since Nursing Homes/Home
# # Health have rich embeddable content while Hospice/IRF are known-thin. A
# # single global floor will under-trust good Nursing Home matches or
# # over-trust thin Hospice ones -- deferred follow-up work, not assumed correct.
# SCORE_FLOOR = 0.45

# NOT_ENOUGH_INFO_MESSAGE = (
#     "I'd like to narrow that down -- could you tell me the type of facility "
#     "(nursing home, home health, hospice, inpatient rehab, or long-term care "
#     "hospital) and/or a city or state?"
# )

# DISCLOSURE_PREFIX = (
#     "I didn't find a CMS-certified match for that in our database, so here's "
#     "what general search turned up instead."
# )


# # Docs Section 9.2/9.3 treat triviality as "generic filler, no real
# # descriptive content" ("good," "nice") -- NOT a word-count cutoff. A
# # word-count threshold would wrongly classify the docs' own worked example
# # ("caring hospice facilities" -> residue "caring", a single word) as trivial,
# # when Section 9.3 explicitly says that query should run the full embed+search
# # pipeline (stages 4-8). Found by testing against the docs' own examples, not
# # assumed on paper.
# _TRIVIAL_RESIDUE_WORDS = {"good", "nice", "ok", "okay", "fine", "great", "decent"}


# def _is_trivial_residue(descriptive_text: str) -> bool:
#     cleaned = descriptive_text.strip().strip(".,!?").lower()
#     if not cleaned:
#         return True
#     return cleaned in _TRIVIAL_RESIDUE_WORDS


# # Found via live eval testing (not assumed common): the LLM sometimes fills
# # city/state with a referential/deictic phrase standing in for "wherever the
# # user is" rather than a real place name -- e.g. "rehabilitation centers in
# # my area" produced city="user's area". Left unnormalized, this reads as a
# # genuine-but-unresolvable place name (scores 0.00 against known cities, same
# # as "Karachi") and triggers a real web-search fallback on nonsense text,
# # instead of being recognized as "no location was actually given" and asking.
# # Deliberately narrow and location-specific -- NOT a general filler-word
# # list; a similar-looking issue in facility_type/descriptive_text (e.g.
# # "caring facilities") is a different mechanism (generic descriptive filler,
# # not a location reference) and is not handled here.
# _LOCATION_REFERENTIAL_FILLERS = {
#     "my area", "your area", "user's area", "users area", "my location",
#     "your location", "near me", "nearby", "around here", "close by",
#     "close to me", "wherever", "here", "around my area", "near my location",
# }


# def _normalize_location_filler(value: str) -> str:
#     cleaned = value.strip().strip(".,!?").lower()
#     return "" if cleaned in _LOCATION_REFERENTIAL_FILLERS else value


# async def _resolve_filters(conn, facility_type: str, city: str, state: str):
#     """
#     Returns (filters: dict, type_unresolved: bool). filters holds only the
#     dimensions that resolved with enough confidence to use.

#     type_unresolved is True when a non-blank facility_type was given but
#     didn't match any of the 5 known types confidently -- the caller routes
#     this to the web fallback (Phase 8) instead of asking the user to pick
#     from an irrelevant list, since a real, valid, just-uncovered type (e.g.
#     "assisted living") scores identically to a garbled attempt at a covered
#     one from here -- *provided* something else (a resolved city/state, or
#     real descriptive text) exists to build that fallback query from; with
#     nothing else at all, the caller asks instead (see search_facilities).
#     Known, deliberate tradeoff: a severely-typo'd covered type that drops
#     below FACILITY_TYPE_CONFIDENCE now also gets a web fallback instead of a
#     chance to self-correct -- measured via evals/dataset.py's near-miss-typo
#     case rather than assumed rare.
#     """
#     filters = {}
#     type_unresolved = False

#     if facility_type.strip():
#         resolved_type, score = await fuzzy_match.correct_facility_type(conn, facility_type)
#         if resolved_type and score >= fuzzy_match.FACILITY_TYPE_CONFIDENCE:
#             log_search(f"  CORRECT facility_type: {facility_type!r} -> {resolved_type!r} "
#                        f"(score={score:.2f}, threshold={fuzzy_match.FACILITY_TYPE_CONFIDENCE}) ACCEPTED")
#             filters["facility_type"] = resolved_type
#         else:
#             log_search(f"  CORRECT facility_type: {facility_type!r} -> {resolved_type!r} "
#                        f"(score={score:.2f}, threshold={fuzzy_match.FACILITY_TYPE_CONFIDENCE}) "
#                        f"REJECTED -- treating as an uncovered type, not asking")
#             type_unresolved = True

#     for field, raw in (("city", city), ("state", state)):
#         if raw.strip():
#             resolved_value, score = await fuzzy_match.correct_known_value(conn, field, raw)
#             if resolved_value and score >= fuzzy_match.KNOWN_VALUE_CONFIDENCE:
#                 log_search(f"  CORRECT {field}: {raw!r} -> {resolved_value!r} "
#                            f"(score={score:.2f}, threshold={fuzzy_match.KNOWN_VALUE_CONFIDENCE}) ACCEPTED")
#                 filters[field] = resolved_value
#             else:
#                 log_search(f"  CORRECT {field}: {raw!r} -> {resolved_value!r} "
#                            f"(score={score:.2f}, threshold={fuzzy_match.KNOWN_VALUE_CONFIDENCE}) REJECTED -- dropped from filter")

#     return filters, type_unresolved


# def _build_qdrant_filter(filters: dict) -> Filter | None:
#     if not filters:
#         return None
#     return Filter(must=[FieldCondition(key=k, match=MatchValue(value=v)) for k, v in filters.items()])


# async def _enrich(conn, facility_ids: list) -> dict:
#     if not facility_ids:
#         return {}
#     rows = await conn.fetch("""
#         SELECT f.facility_id, f.name, f.address_line1, f.city, f.state, f.zip_code,
#                f.phone, f.facility_type, f.ownership_type, d.attributes
#         FROM infomary_facilities f
#         JOIN infomary_facility_detail d ON d.facility_id = f.facility_id
#         WHERE f.facility_id = ANY($1::uuid[])
#     """, facility_ids)
#     # Qdrant's ScoredPoint.id is a plain str; asyncpg returns facility_id as an
#     # asyncpg.pgproto.pgproto.UUID object. str() both sides so the dict lookup
#     # in search_facilities() (keyed by Qdrant's string ids) actually matches.
#     return {str(r["facility_id"]): dict(r) for r in rows}


# async def _supabase_filtered_query(conn, filters: dict) -> list:
#     clauses, params = [], []
#     for i, (field, value) in enumerate(filters.items(), start=1):
#         clauses.append(f"{field} = ${i}")
#         params.append(value)
#     where = " AND ".join(clauses)
#     rows = await conn.fetch(f"""
#         SELECT f.facility_id, f.name, f.address_line1, f.city, f.state, f.zip_code,
#                f.phone, f.facility_type, f.ownership_type, d.attributes
#         FROM infomary_facilities f
#         JOIN infomary_facility_detail d ON d.facility_id = f.facility_id
#         WHERE {where}
#         ORDER BY f.name
#         LIMIT {RESULT_LIMIT}
#     """, *params)
#     return [dict(r) for r in rows]


# # Phase 11 -- nursing_staffing_agency is a B2B vendor supplying nurses TO
# # facilities, not a residence or direct-care location. content_templates.py's
# # builder for this type already says so, but that text ONLY ever feeds the
# # Qdrant embedding corpus (embed_sync.py) -- it's never read by search.py, so
# # it never actually reached a real card or reply. Stated here too, in what
# # _row_to_card/_intro_text actually return, so it can't be silently dropped
# # by the LLM's own paraphrase (same reasoning as Phase 10's DISCLOSURE_PREFIX
# # server-side enforcement, applied at the source instead of retrofitted).
# _STAFFING_AGENCY_NOTE = (
#     "This is a staffing agency that supplies nursing staff to facilities -- "
#     "not a residence or direct-care location."
# )


# def _highlight(facility_type: str, attributes: dict) -> str | None:
#     if facility_type == "nursing_home":
#         rating = (attributes.get("ratings") or {}).get("overall")
#         if rating is not None:
#             return f"overall rating {rating}/5"
#     elif facility_type == "home_health":
#         rating = attributes.get("quality_star_rating")
#         if rating is not None:
#             return f"quality rating {rating}/5"
#     elif facility_type == "ltch":
#         beds = attributes.get("total_beds")
#         if beds is not None:
#             return f"{beds} total beds"
#     return None


# def _row_to_card(row: dict, facility_type: str) -> dict:
#     """
#     One clean, JSON-safe dict per facility -- this is what the frontend
#     renders as a flashcard. Detailed facts live here, not in the text reply
#     the LLM produces (see _intro_text), so the LLM never has to reproduce
#     structured data in its own words. Tagged cms_certified directly (Phase 8)
#     since a single search_facilities() call can now return a mix of certified
#     and web-fallback cards depending on which path fired -- main.py can no
#     longer blanket-tag by "which tool got called."
#     """
#     attributes = row.get("attributes")
#     if isinstance(attributes, str):
#         attributes = json.loads(attributes) if attributes else {}
#     return {
#         "source": "cms_certified",
#         "name": row["name"],
#         "facility_type_label": TYPE_LABELS.get(facility_type, facility_type),
#         "city": row.get("city"),
#         "state": row.get("state"),
#         "phone": row.get("phone"),
#         "highlight": _highlight(facility_type, attributes or {}),
#         "note": _STAFFING_AGENCY_NOTE if facility_type == "nursing_staffing_agency" else None,
#     }


# def _organic_to_card(r: dict) -> dict:
#     """The web-fallback equivalent of _row_to_card -- same card contract, tagged not_certified."""
#     return {
#         "source": "not_certified",
#         "title": r.get("title"),
#         "snippet": r.get("snippet"),
#         "url": r.get("link"),
#     }


# def _fallback_query(facility_type: str, city: str, state: str, descriptive_text: str) -> str:
#     """
#     Builds a web-search string from whatever raw input is non-blank. Uses the
#     user's own raw wording (not the fuzzy-corrected CMS-internal values) --
#     natural phrasing is what a general web search wants, not our internal
#     type_key/state-code normalization.
#     """
#     parts = [p.strip() for p in (facility_type, descriptive_text) if p.strip()]
#     base = " ".join(parts) if parts else "senior care facilities"
#     loc_bits = [p.strip() for p in (city, state) if p.strip()]
#     return f"{base} in {', '.join(loc_bits)}" if loc_bits else base


# def _intro_text(rows: list, filters: dict, low_confidence: bool) -> str:
#     """
#     Short text for the LLM to build its reply around -- replaces the old
#     enumerated list, since facility detail is now shown to the user as
#     flashcards (built from the same `rows` via _row_to_card). Callers route
#     empty rows through _cards_or_web_fallback instead of here, except Stage
#     7's low-confidence branch (deliberately unchanged by Phase 8) which could
#     in rare cases still pass empty rows -- this guard is kept for that.
#     """
#     if not rows:
#         return "I couldn't find any facilities matching those criteria."
#     type_label = TYPE_LABELS.get(filters.get("facility_type"))
#     loc_bits = [v for v in (filters.get("city"), filters.get("state")) if v]
#     loc = f" near {', '.join(loc_bits)}" if loc_bits else ""
#     if type_label:
#         text = f"I found {len(rows)} matching {type_label} option{'s' if len(rows) != 1 else ''}{loc}."
#     else:
#         text = f"I found {len(rows)} matching facilit{'ies' if len(rows) != 1 else 'y'}{loc}."
#     if low_confidence:
#         text += (" (Matched on facility type/location -- the descriptive part of your request didn't "
#                  "have a strong enough match to rank by, so this isn't ordered by how well it fits that "
#                  "description.)")
#     if filters.get("facility_type") == "nursing_staffing_agency":
#         text += f" Note: {_STAFFING_AGENCY_NOTE}"
#     return text


# async def _cards_or_web_fallback(
#     reason: str, rows: list, filters: dict,
#     facility_type: str, city: str, state: str, descriptive_text: str,
#     low_confidence: bool = False,
# ) -> tuple[str, list[dict]]:
#     """
#     The Phase 8 choke point: real rows -> real certified cards, same as
#     always. Empty rows -> an internal web-search fallback, already worded
#     with the required disclosure sentence and already tagged not_certified,
#     so the calling LLM never has to remember to say it or decide to call a
#     second tool. `reason` (e.g. "unresolved_type", "zero_supabase_rows",
#     "zero_qdrant_points") is logged alongside this pipeline's existing
#     stage/score/row-count logging -- three genuinely different trigger
#     conditions land here, and without a distinct logged reason a future "why
#     did this get a web fallback" debugging session can't tell them apart.
#     """
#     if rows:
#         cards = [_row_to_card(r, r["facility_type"]) for r in rows]
#         return _intro_text(rows, filters, low_confidence=low_confidence), cards

#     query = _fallback_query(facility_type, city, state, descriptive_text)
#     log_search(f"  WEB FALLBACK: reason={reason} | query={query!r}")
#     _text, organic = await web_search(query)

#     if not organic:
#         log_search(f"  WEB FALLBACK: reason={reason} -- zero web results either")
#         return (
#             f"{DISCLOSURE_PREFIX} Unfortunately general search didn't turn up anything either -- "
#             "could you tell me a bit more (a city/state, or a different facility type)?",
#             [],
#         )

#     cards = [_organic_to_card(r) for r in organic]
#     log_search(f"  WEB FALLBACK: reason={reason} -- {len(cards)} result(s) returned")
#     intro = f"{DISCLOSURE_PREFIX} I found {len(cards)} option{'s' if len(cards) != 1 else ''}."
#     return intro, cards


# async def search_facilities(
#     facility_type: str = "", city: str = "", state: str = "", descriptive_text: str = ""
# ) -> tuple[str, list[dict] | None]:
#     log_search(f"=== facility_search call ===")
#     log_search(f"  INPUT: facility_type={facility_type!r} city={city!r} state={state!r} descriptive_text={descriptive_text!r}")

#     # Normalize referential location filler ("my area", "nearby", ...) to
#     # blank before ANY guard or resolution logic below sees city/state --
#     # every downstream check (location_given, the type-only-ask guard,
#     # _resolve_filters' fuzzy match, _fallback_query) then treats it exactly
#     # as if the user/LLM had never filled it in, rather than as a real-but-
#     # unresolvable place name.
#     normalized_city, normalized_state = _normalize_location_filler(city), _normalize_location_filler(state)
#     if normalized_city != city or normalized_state != state:
#         log_search(f"  NORMALIZE: referential location filler treated as blank -- city={city!r}->{normalized_city!r} state={state!r}->{normalized_state!r}")
#     city, state = normalized_city, normalized_state

#     try:
#         async with get_db_connection() as conn:
#             # Guard: nothing at all to go on. Nothing to search on, certified
#             # or web -- asking is still correct, not a fallback trigger.
#             if not any(s.strip() for s in (facility_type, city, state, descriptive_text)):
#                 log_search("  STOP: all inputs blank -- asking user instead of searching")
#                 return NOT_ENOUGH_INFO_MESSAGE, None

#             # Stage 2 + 3.
#             filters, type_unresolved = await _resolve_filters(conn, facility_type, city, state)
#             if type_unresolved:
#                 # A city/state/descriptive_text still resolved -> enough to
#                 # build a real fallback query from (e.g. "assisted living in
#                 # Miami"). Nothing else at all -> vague filler landed in the
#                 # facility_type slot (e.g. "caring facilities") -- asking is
#                 # cheaper and safer than a blind web search on just that text.
#                 has_other_anchor = bool(filters) or not _is_trivial_residue(descriptive_text)
#                 if not has_other_anchor:
#                     log_search("  STOP: facility_type unresolved and nothing else to anchor on -- asking instead of a blind fallback")
#                     return NOT_ENOUGH_INFO_MESSAGE, None
#                 log_search("  PATH: facility_type unresolved, other anchor present -- routing to web fallback")
#                 return await _cards_or_web_fallback(
#                     "unresolved_type", [], filters, facility_type, city, state, descriptive_text
#                 )

#             # A city/state was given but didn't fuzzy-match anything we know
#             # (our CMS data is US-only) -- dropping it silently would widen an
#             # intentionally-scoped search into an unscoped nationwide one (e.g.
#             # "nursing homes in Karachi" returning random US facilities).
#             # Route to the same web fallback used for unresolved types instead.
#             location_given = bool(city.strip()) or bool(state.strip())
#             location_unresolved = location_given and "city" not in filters and "state" not in filters
#             if location_unresolved:
#                 log_search("  PATH: location unresolved -- routing to web fallback instead of searching without a location constraint")
#                 return await _cards_or_web_fallback(
#                     "unresolved_location", [], filters, facility_type, city, state, descriptive_text
#                 )

#             log_search(f"  FILTERS RESOLVED: {filters}")
#             query_filter = _build_qdrant_filter(filters)

#             trivial_residue = _is_trivial_residue(descriptive_text)
#             log_search(f"  RESIDUE CHECK: descriptive_text={descriptive_text!r} trivial={trivial_residue}")

#             # Facility type is the only resolved filter -- city and state were never
#             # given at all. Ask for a location before doing anything else, regardless
#             # of whether descriptive_text is blank or rich -- consistent, predictable
#             # behavior: "facility type alone, no location" always prompts for a
#             # location, full stop, no exception based on how much text surrounds it.
#             #
#             # This also means a facility-type-only query never reaches the embed+Qdrant
#             # pipeline, saving an unnecessary Fireworks/Qdrant call on a query that's
#             # about to get asked to clarify anyway -- on top of fixing the original bug
#             # (a real card shown alongside a contradictory "what's your city?" follow-up).
#             if filters and not city.strip() and not state.strip():
#                 type_label = TYPE_LABELS.get(filters["facility_type"], facility_type)
#                 log_search("  STOP: facility_type-only, no location given -- asking for city/state before any search")
#                 return f"I can help find {type_label} options -- what city or state should I search in?", None

#             # Stage 4: trivial residue, nothing resolved -> nothing usable at all.
#             if trivial_residue and not filters:
#                 log_search("  STOP: trivial residue + no filters resolved -- nothing to search on")
#                 return NOT_ENOUGH_INFO_MESSAGE, None

#             # Stage 4: trivial residue, at least one filter -> Supabase-only, skip Qdrant entirely.
#             if trivial_residue:
#                 log_search(f"  PATH: Supabase-only (no embedding/Qdrant call) | WHERE {filters}")
#                 rows = await _supabase_filtered_query(conn, filters)
#                 log_search(f"  SOURCE: infomary_facilities JOIN infomary_facility_detail -- {len(rows)} row(s) returned")
#                 return await _cards_or_web_fallback(
#                     "zero_supabase_rows", rows, filters, facility_type, city, state, descriptive_text
#                 )

#             # Anchor guard (code-level, Stage 5 gate): non-trivial residue but
#             # zero structured filters resolved -- do not run an unfiltered
#             # semantic search across the whole collection. This is the
#             # "fuzzy-only, no anchor -> ask first" rule enforced in code, not
#             # left to the system prompt alone. Nothing to build a web query
#             # from either, so asking is still correct here too.
#             if not filters:
#                 log_search("  STOP: descriptive text given but zero structured filters resolved -- refusing an unfiltered nationwide search")
#                 return NOT_ENOUGH_INFO_MESSAGE, None

#             # Stage 5 + 6.
#             log_search(f"  PATH: full pipeline (embed + Qdrant search)")
#             vectors = await embed_texts([descriptive_text])
#             log_search(f"  EMBEDDING: model={EMBEDDING_MODEL} | input={descriptive_text!r} | vector_dims={len(vectors[0])}")

#             points = await qdrant_index.search_points(vectors[0], query_filter, limit=RESULT_LIMIT)
#             log_search(f"  QDRANT SEARCH: collection={qdrant_index.COLLECTION_NAME} | filter={filters} | limit={RESULT_LIMIT} "
#                        f"-> {len(points)} point(s) returned")
#             for p in points:
#                 log_search(f"    point facility_id={p.id} score={p.score:.4f}")

#             if not points:
#                 log_search("  RESULT: zero Qdrant matches for this filter -- routing to web fallback")
#                 return await _cards_or_web_fallback(
#                     "zero_qdrant_points", [], filters, facility_type, city, state, descriptive_text
#                 )

#             top_score = points[0].score
#             log_search(f"  THRESHOLD CHECK: top_score={top_score:.4f} vs SCORE_FLOOR={SCORE_FLOOR} "
#                        f"-> {'PASS (ranked results trusted)' if top_score >= SCORE_FLOOR else 'FAIL (falling back to unranked Supabase list)'}")

#             # Stage 7: low confidence -> fall back to a plain structured filter
#             # query, NOT the web fallback -- real matches exist here, just not
#             # semantically confident ones. Deliberately unchanged from before
#             # Phase 8: this is a ranking-confidence issue, not a coverage/
#             # data-absence one, so it stays a certified (if unranked) result.
#             # filters is guaranteed non-empty here (anchor guard above), so
#             # this can never degrade into an unfiltered random list.
#             if top_score < SCORE_FLOOR:
#                 rows = await _supabase_filtered_query(conn, filters)
#                 log_search(f"  SOURCE: infomary_facilities JOIN infomary_facility_detail (fallback, unranked) -- {len(rows)} row(s) returned")
#                 cards = [_row_to_card(r, r["facility_type"]) for r in rows]
#                 return _intro_text(rows, filters, low_confidence=True), cards

#             # Stage 8: enrich from Supabase, preserving Qdrant's ranking order.
#             facility_ids = [p.id for p in points]
#             enriched = await _enrich(conn, facility_ids)
#             log_search(f"  SOURCE: infomary_facilities JOIN infomary_facility_detail WHERE facility_id = ANY(...) "
#                        f"-- {len(enriched)}/{len(facility_ids)} facility_id(s) matched (Qdrant-ranked order preserved)")
#             rows = [enriched[fid] for fid in facility_ids if fid in enriched]
#             cards = [_row_to_card(r, r["facility_type"]) for r in rows]
#             return _intro_text(rows, filters, low_confidence=False), cards

#     except Exception as e:
#         log_warn(f"facility_search | unexpected failure | {type(e).__name__}: {e}")
#         return "Sorry, I couldn't search facility data right now -- please try again in a moment.", None





































"""
Phase 4 -- the query pipeline (architecture docs, Section 9), implemented as a
single function an agent-facing StructuredTool calls directly (see
tools/agent_tools.py's facility_search). Deliberately skips the docs' Stage 1
("Query Parsing") as a separate LLM call -- the calling agent already performs
that extraction via this function's own arguments, since the actual entrypoint
in this codebase is a tool-calling agent, not a standalone free-text endpoint.

Stage numbers below reference architecture docs Section 9.2.

Phase 5 change: search_facilities() now returns (text, cards) instead of a
plain str. `text` is a short line for the LLM to build its reply around;
`cards` is the structured facility data (or None when no search ran at all,
e.g. a clarifying question) that the frontend renders as flashcards. The LLM
never has to reproduce facility details in prose -- see agent_tools.py's
content_and_artifact wiring.

Phase 8 change: eval-driven testing showed the LLM was unreliable at judging
"is this facility type covered, should I call google_search instead, what do
I say about it" via prompt rules alone -- and a prompt fix for one case
(EMERGENCY PROTOCOL precedence) measurably regressed an unrelated case,
confirming the prompt was carrying decisions that belong in code. So
search_facilities() now decides for itself: an unrecognized facility type or
a genuinely empty search result triggers an internal web-search fallback
(_cards_or_web_fallback), already worded with the required disclosure
sentence and already tagged with the right card source -- the calling LLM
just needs to call this one tool whenever the user wants a facility, full
stop. Stage 7's low-confidence-*ranking* branch is deliberately NOT part of
this -- that's a different situation (real matches exist, just not
semantically confident), not a coverage/data-absence one.
"""
import json

from database import get_db_connection
from logger import log_search, log_warn
from tools.facility_search import fuzzy_match, qdrant_index
from tools.facility_search.content_templates import TYPE_LABELS
from tools.facility_search.embeddings import embed_texts, EMBEDDING_MODEL
from tools.web_search import web_search

from qdrant_client.models import Filter, FieldCondition, MatchValue

RESULT_LIMIT = 5

# One flat global constant -- a known, explicitly-flagged simplification, not a
# settled design decision. The architecture docs' own Open Items (Section 11)
# call for per-facility-type threshold calibration, since Nursing Homes/Home
# Health have rich embeddable content while Hospice/IRF are known-thin. A
# single global floor will under-trust good Nursing Home matches or
# over-trust thin Hospice ones -- deferred follow-up work, not assumed correct.
SCORE_FLOOR = 0.45

NOT_ENOUGH_INFO_MESSAGE = (
    "I'd like to narrow that down -- could you tell me the type of facility "
    "(nursing home, home health, hospice, inpatient rehab, or long-term care "
    "hospital) and/or a city or state?"
)

DISCLOSURE_PREFIX = (
    "I didn't find a CMS-certified match for that in our database, so here's "
    "what general search turned up instead."
)


# Docs Section 9.2/9.3 treat triviality as "generic filler, no real
# descriptive content" ("good," "nice") -- NOT a word-count cutoff. A
# word-count threshold would wrongly classify the docs' own worked example
# ("caring hospice facilities" -> residue "caring", a single word) as trivial,
# when Section 9.3 explicitly says that query should run the full embed+search
# pipeline (stages 4-8). Found by testing against the docs' own examples, not
# assumed on paper.
_TRIVIAL_RESIDUE_WORDS = {"good", "nice", "ok", "okay", "fine", "great", "decent"}


def _is_trivial_residue(descriptive_text: str) -> bool:
    cleaned = descriptive_text.strip().strip(".,!?").lower()
    if not cleaned:
        return True
    return cleaned in _TRIVIAL_RESIDUE_WORDS


# Found via live eval testing (not assumed common): the LLM sometimes fills
# city/state with a referential/deictic phrase standing in for "wherever the
# user is" rather than a real place name -- e.g. "rehabilitation centers in
# my area" produced city="user's area". Left unnormalized, this reads as a
# genuine-but-unresolvable place name (scores 0.00 against known cities, same
# as "Karachi") and triggers a real web-search fallback on nonsense text,
# instead of being recognized as "no location was actually given" and asking.
# Deliberately narrow and location-specific -- NOT a general filler-word
# list; a similar-looking issue in facility_type/descriptive_text (e.g.
# "caring facilities") is a different mechanism (generic descriptive filler,
# not a location reference) and is not handled here.
_LOCATION_REFERENTIAL_FILLERS = {
    "my area", "your area", "user's area", "users area", "my location",
    "your location", "near me", "nearby", "around here", "close by",
    "close to me", "wherever", "here", "around my area", "near my location",
}


def _normalize_location_filler(value: str) -> str:
    cleaned = value.strip().strip(".,!?").lower()
    return "" if cleaned in _LOCATION_REFERENTIAL_FILLERS else value


async def _resolve_filters(conn, facility_type: str, city: str, state: str):
    """
    Returns (filters: dict, type_unresolved: bool). filters holds only the
    dimensions that resolved with enough confidence to use.

    type_unresolved is True when a non-blank facility_type was given but
    didn't match any of the 5 known types confidently -- the caller routes
    this to the web fallback (Phase 8) instead of asking the user to pick
    from an irrelevant list, since a real, valid, just-uncovered type (e.g.
    "assisted living") scores identically to a garbled attempt at a covered
    one from here -- *provided* something else (a resolved city/state, or
    real descriptive text) exists to build that fallback query from; with
    nothing else at all, the caller asks instead (see search_facilities).
    Known, deliberate tradeoff: a severely-typo'd covered type that drops
    below FACILITY_TYPE_CONFIDENCE now also gets a web fallback instead of a
    chance to self-correct -- measured via evals/dataset.py's near-miss-typo
    case rather than assumed rare.
    """
    filters = {}
    type_unresolved = False

    if facility_type.strip():
        resolved_type, score = await fuzzy_match.correct_facility_type(conn, facility_type)
        if resolved_type and score >= fuzzy_match.FACILITY_TYPE_CONFIDENCE:
            log_search(f"  CORRECT facility_type: {facility_type!r} -> {resolved_type!r} "
                       f"(score={score:.2f}, threshold={fuzzy_match.FACILITY_TYPE_CONFIDENCE}) ACCEPTED")
            filters["facility_type"] = resolved_type
        else:
            log_search(f"  CORRECT facility_type: {facility_type!r} -> {resolved_type!r} "
                       f"(score={score:.2f}, threshold={fuzzy_match.FACILITY_TYPE_CONFIDENCE}) "
                       f"REJECTED -- treating as an uncovered type, not asking")
            type_unresolved = True

    for field, raw in (("city", city), ("state", state)):
        if raw.strip():
            resolved_value, score = await fuzzy_match.correct_known_value(conn, field, raw)
            if resolved_value and score >= fuzzy_match.KNOWN_VALUE_CONFIDENCE:
                log_search(f"  CORRECT {field}: {raw!r} -> {resolved_value!r} "
                           f"(score={score:.2f}, threshold={fuzzy_match.KNOWN_VALUE_CONFIDENCE}) ACCEPTED")
                filters[field] = resolved_value
            else:
                log_search(f"  CORRECT {field}: {raw!r} -> {resolved_value!r} "
                           f"(score={score:.2f}, threshold={fuzzy_match.KNOWN_VALUE_CONFIDENCE}) REJECTED -- dropped from filter")

    return filters, type_unresolved


def _build_qdrant_filter(filters: dict) -> Filter | None:
    if not filters:
        return None
    return Filter(must=[FieldCondition(key=k, match=MatchValue(value=v)) for k, v in filters.items()])


async def _enrich(conn, facility_ids: list) -> dict:
    if not facility_ids:
        return {}
    rows = await conn.fetch("""
        SELECT f.facility_id, f.name, f.address_line1, f.city, f.state, f.zip_code,
               f.phone, f.facility_type, f.ownership_type, d.attributes
        FROM infomary_facilities f
        JOIN infomary_facility_detail d ON d.facility_id = f.facility_id
        WHERE f.facility_id = ANY($1::uuid[])
    """, facility_ids)
    # Qdrant's ScoredPoint.id is a plain str; asyncpg returns facility_id as an
    # asyncpg.pgproto.pgproto.UUID object. str() both sides so the dict lookup
    # in search_facilities() (keyed by Qdrant's string ids) actually matches.
    return {str(r["facility_id"]): dict(r) for r in rows}


async def _supabase_filtered_query(conn, filters: dict) -> list:
    clauses, params = [], []
    for i, (field, value) in enumerate(filters.items(), start=1):
        clauses.append(f"{field} = ${i}")
        params.append(value)
    where = " AND ".join(clauses)
    rows = await conn.fetch(f"""
        SELECT f.facility_id, f.name, f.address_line1, f.city, f.state, f.zip_code,
               f.phone, f.facility_type, f.ownership_type, d.attributes
        FROM infomary_facilities f
        JOIN infomary_facility_detail d ON d.facility_id = f.facility_id
        WHERE {where}
        ORDER BY f.name
        LIMIT {RESULT_LIMIT}
    """, *params)
    return [dict(r) for r in rows]


# Phase 11 -- nursing_staffing_agency is a B2B vendor supplying nurses TO
# facilities, not a residence or direct-care location. content_templates.py's
# builder for this type already says so, but that text ONLY ever feeds the
# Qdrant embedding corpus (embed_sync.py) -- it's never read by search.py, so
# it never actually reached a real card or reply. Stated here too, in what
# _row_to_card/_intro_text actually return, so it can't be silently dropped
# by the LLM's own paraphrase (same reasoning as Phase 10's DISCLOSURE_PREFIX
# server-side enforcement, applied at the source instead of retrofitted).
_STAFFING_AGENCY_NOTE = (
    "This is a staffing agency that supplies nursing staff to facilities -- "
    "not a residence or direct-care location."
)


def _title_case_label(facility_type: str) -> str:
    """
    Card-facing display label, e.g. "Nursing Home", "Home Health Agency".

    str.title() would mangle the hyphenated labels ("long-term care hospital"
    -> "Long-Term Care Hospital" is fine, but it also uppercases after any
    apostrophe), so capitalize word-by-word on spaces only and leave the rest
    of each word untouched.
    """
    label = TYPE_LABELS.get(facility_type, facility_type)
    return " ".join(w[:1].upper() + w[1:] for w in label.split(" "))


def _highlight(attributes: dict) -> str | None:
    """
    Short badge text for the card, read from the attributes JSONB.

    No longer takes facility_type: overall_rating is a shared base attribute
    written for every type (schemas.py's _BASE_PROPERTIES), so the same key
    works everywhere and the per-type branching this used to do was what hid
    the broken key paths described below.

    The key paths here are the ones the ETL actually writes (see mappings.py's
    COMBINED_MAPPINGS + schemas.py) -- previously this read "ratings"."overall"
    and "quality_star_rating", neither of which exists anywhere in the stored
    JSONB, so every card came back with highlight=null. Verified against live
    data: overall_rating is populated for 13,776 nursing homes and 6,687 home
    health agencies, and is the ONLY rating either type carries (no separate
    home-health quality field exists).

    The 5-point CMS scale is 1-5, but 125 rows carry a 9.0 -- a source
    sentinel for "not available", not a real 9-star rating. Rendering that
    verbatim would show "9/5" on a card, so anything outside 1-5 is dropped.

    The ltch branch is gone: that type is retired (seed.py) and receives zero
    rows, and "total_beds" was never a key the combined-source ETL wrote --
    nursing homes store bed counts under "total_certified_beds".
    """
    rating = attributes.get("overall_rating")
    if isinstance(rating, (int, float)) and 1 <= rating <= 5:
        # Ratings are stored as floats (1.0, 2.0, ...) but are whole numbers on
        # a 1-5 scale -- render "4/5", not "4.0/5".
        return f"{rating:g}/5 CMS rating"
    return None


def _row_to_card(row: dict, facility_type: str) -> dict:
    """
    One clean, JSON-safe dict per facility -- this is what the frontend
    renders as a flashcard. Detailed facts live here, not in the text reply
    the LLM produces (see _intro_text), so the LLM never has to reproduce
    structured data in its own words. Tagged cms_certified directly (Phase 8)
    since a single search_facilities() call can now return a mix of certified
    and web-fallback cards depending on which path fired -- main.py can no
    longer blanket-tag by "which tool got called."
    """
    attributes = row.get("attributes")
    if isinstance(attributes, str):
        attributes = json.loads(attributes) if attributes else {}
    return {
        # `facility_id` here is `infomary_facilities.facility_id`, which is a
        # passthrough of the source row's own uuid (see etl.py) -- the SAME
        # id space as `facilities.source_uuid` on the main app schema, NOT
        # `facilities.id`. GET /facilities/{facility_id} on the main API
        # resolves both, so this id is safe to send to the frontend as-is
        # and use directly for the detail screen. Without this field the
        # client has nothing but name/city to re-look-up the facility with,
        # which is exactly what causes a different, wrong facility's detail
        # to load for a search result card.
        "id": str(row["facility_id"]),
        "source": "cms_certified",
        "name": row["name"],
        # TYPE_LABELS is deliberately lowercase -- it's built for mid-sentence
        # prose ("I found 5 matching nursing home options", "A for-profit
        # nursing home..."). Title-case it HERE rather than at the source, so
        # the card matches the documented API contract without changing the
        # wording of every sentence that shares the same dict.
        "facility_type_label": _title_case_label(facility_type),
        "address_line1": row.get("address_line1"),
        "city": row.get("city"),
        "state": row.get("state"),
        "zip_code": row.get("zip_code"),
        "phone": row.get("phone"),
        "ownership_type": row.get("ownership_type"),
        "highlight": _highlight(attributes or {}),
        "note": _STAFFING_AGENCY_NOTE if facility_type == "nursing_staffing_agency" else None,
    }


def _organic_to_card(r: dict) -> dict:
    """The web-fallback equivalent of _row_to_card -- same card contract, tagged not_certified."""
    return {
        "source": "not_certified",
        "title": r.get("title"),
        "snippet": r.get("snippet"),
        "url": r.get("link"),
    }


def _fallback_query(facility_type: str, city: str, state: str, descriptive_text: str) -> str:
    """
    Builds a web-search string from whatever raw input is non-blank. Uses the
    user's own raw wording (not the fuzzy-corrected CMS-internal values) --
    natural phrasing is what a general web search wants, not our internal
    type_key/state-code normalization.
    """
    parts = [p.strip() for p in (facility_type, descriptive_text) if p.strip()]
    base = " ".join(parts) if parts else "senior care facilities"
    loc_bits = [p.strip() for p in (city, state) if p.strip()]
    return f"{base} in {', '.join(loc_bits)}" if loc_bits else base


def _intro_text(rows: list, filters: dict, low_confidence: bool) -> str:
    """
    Short text for the LLM to build its reply around -- replaces the old
    enumerated list, since facility detail is now shown to the user as
    flashcards (built from the same `rows` via _row_to_card). Callers route
    empty rows through _cards_or_web_fallback instead of here, except Stage
    7's low-confidence branch (deliberately unchanged by Phase 8) which could
    in rare cases still pass empty rows -- this guard is kept for that.
    """
    if not rows:
        return "I couldn't find any facilities matching those criteria."
    type_label = TYPE_LABELS.get(filters.get("facility_type"))
    loc_bits = [v for v in (filters.get("city"), filters.get("state")) if v]
    loc = f" near {', '.join(loc_bits)}" if loc_bits else ""
    if type_label:
        text = f"I found {len(rows)} matching {type_label} option{'s' if len(rows) != 1 else ''}{loc}."
    else:
        text = f"I found {len(rows)} matching facilit{'ies' if len(rows) != 1 else 'y'}{loc}."
    if low_confidence:
        text += (" (Matched on facility type/location -- the descriptive part of your request didn't "
                 "have a strong enough match to rank by, so this isn't ordered by how well it fits that "
                 "description.)")
    if filters.get("facility_type") == "nursing_staffing_agency":
        text += f" Note: {_STAFFING_AGENCY_NOTE}"
    return text


async def _cards_or_web_fallback(
    reason: str, rows: list, filters: dict,
    facility_type: str, city: str, state: str, descriptive_text: str,
    low_confidence: bool = False,
) -> tuple[str, list[dict]]:
    """
    The Phase 8 choke point: real rows -> real certified cards, same as
    always. Empty rows -> an internal web-search fallback, already worded
    with the required disclosure sentence and already tagged not_certified,
    so the calling LLM never has to remember to say it or decide to call a
    second tool. `reason` (e.g. "unresolved_type", "zero_supabase_rows",
    "zero_qdrant_points") is logged alongside this pipeline's existing
    stage/score/row-count logging -- three genuinely different trigger
    conditions land here, and without a distinct logged reason a future "why
    did this get a web fallback" debugging session can't tell them apart.
    """
    if rows:
        cards = [_row_to_card(r, r["facility_type"]) for r in rows]
        return _intro_text(rows, filters, low_confidence=low_confidence), cards

    query = _fallback_query(facility_type, city, state, descriptive_text)
    log_search(f"  WEB FALLBACK: reason={reason} | query={query!r}")
    _text, organic = await web_search(query)

    if not organic:
        log_search(f"  WEB FALLBACK: reason={reason} -- zero web results either")
        return (
            f"{DISCLOSURE_PREFIX} Unfortunately general search didn't turn up anything either -- "
            "could you tell me a bit more (a city/state, or a different facility type)?",
            [],
        )

    cards = [_organic_to_card(r) for r in organic]
    log_search(f"  WEB FALLBACK: reason={reason} -- {len(cards)} result(s) returned")
    intro = f"{DISCLOSURE_PREFIX} I found {len(cards)} option{'s' if len(cards) != 1 else ''}."
    return intro, cards


async def search_facilities(
    facility_type: str = "", city: str = "", state: str = "", descriptive_text: str = ""
) -> tuple[str, list[dict] | None]:
    log_search(f"=== facility_search call ===")
    log_search(f"  INPUT: facility_type={facility_type!r} city={city!r} state={state!r} descriptive_text={descriptive_text!r}")

    # Normalize referential location filler ("my area", "nearby", ...) to
    # blank before ANY guard or resolution logic below sees city/state --
    # every downstream check (location_given, the type-only-ask guard,
    # _resolve_filters' fuzzy match, _fallback_query) then treats it exactly
    # as if the user/LLM had never filled it in, rather than as a real-but-
    # unresolvable place name.
    normalized_city, normalized_state = _normalize_location_filler(city), _normalize_location_filler(state)
    if normalized_city != city or normalized_state != state:
        log_search(f"  NORMALIZE: referential location filler treated as blank -- city={city!r}->{normalized_city!r} state={state!r}->{normalized_state!r}")
    city, state = normalized_city, normalized_state

    try:
        async with get_db_connection() as conn:
            # Guard: nothing at all to go on. Nothing to search on, certified
            # or web -- asking is still correct, not a fallback trigger.
            if not any(s.strip() for s in (facility_type, city, state, descriptive_text)):
                log_search("  STOP: all inputs blank -- asking user instead of searching")
                return NOT_ENOUGH_INFO_MESSAGE, None

            # Stage 2 + 3.
            filters, type_unresolved = await _resolve_filters(conn, facility_type, city, state)
            if type_unresolved:
                # A city/state/descriptive_text still resolved -> enough to
                # build a real fallback query from (e.g. "assisted living in
                # Miami"). Nothing else at all -> vague filler landed in the
                # facility_type slot (e.g. "caring facilities") -- asking is
                # cheaper and safer than a blind web search on just that text.
                has_other_anchor = bool(filters) or not _is_trivial_residue(descriptive_text)
                if not has_other_anchor:
                    log_search("  STOP: facility_type unresolved and nothing else to anchor on -- asking instead of a blind fallback")
                    return NOT_ENOUGH_INFO_MESSAGE, None
                log_search("  PATH: facility_type unresolved, other anchor present -- routing to web fallback")
                return await _cards_or_web_fallback(
                    "unresolved_type", [], filters, facility_type, city, state, descriptive_text
                )

            # A city/state was given but didn't fuzzy-match anything we know
            # (our CMS data is US-only) -- dropping it silently would widen an
            # intentionally-scoped search into an unscoped nationwide one (e.g.
            # "nursing homes in Karachi" returning random US facilities).
            # Route to the same web fallback used for unresolved types instead.
            location_given = bool(city.strip()) or bool(state.strip())
            location_unresolved = location_given and "city" not in filters and "state" not in filters
            if location_unresolved:
                log_search("  PATH: location unresolved -- routing to web fallback instead of searching without a location constraint")
                return await _cards_or_web_fallback(
                    "unresolved_location", [], filters, facility_type, city, state, descriptive_text
                )

            log_search(f"  FILTERS RESOLVED: {filters}")
            query_filter = _build_qdrant_filter(filters)

            trivial_residue = _is_trivial_residue(descriptive_text)
            log_search(f"  RESIDUE CHECK: descriptive_text={descriptive_text!r} trivial={trivial_residue}")

            # Facility type is the only resolved filter -- city and state were never
            # given at all. Ask for a location before doing anything else, regardless
            # of whether descriptive_text is blank or rich -- consistent, predictable
            # behavior: "facility type alone, no location" always prompts for a
            # location, full stop, no exception based on how much text surrounds it.
            #
            # This also means a facility-type-only query never reaches the embed+Qdrant
            # pipeline, saving an unnecessary Fireworks/Qdrant call on a query that's
            # about to get asked to clarify anyway -- on top of fixing the original bug
            # (a real card shown alongside a contradictory "what's your city?" follow-up).
            if filters and not city.strip() and not state.strip():
                type_label = TYPE_LABELS.get(filters["facility_type"], facility_type)
                log_search("  STOP: facility_type-only, no location given -- asking for city/state before any search")
                return f"I can help find {type_label} options -- what city or state should I search in?", None

            # Stage 4: trivial residue, nothing resolved -> nothing usable at all.
            if trivial_residue and not filters:
                log_search("  STOP: trivial residue + no filters resolved -- nothing to search on")
                return NOT_ENOUGH_INFO_MESSAGE, None

            # Stage 4: trivial residue, at least one filter -> Supabase-only, skip Qdrant entirely.
            if trivial_residue:
                log_search(f"  PATH: Supabase-only (no embedding/Qdrant call) | WHERE {filters}")
                rows = await _supabase_filtered_query(conn, filters)
                log_search(f"  SOURCE: infomary_facilities JOIN infomary_facility_detail -- {len(rows)} row(s) returned")
                return await _cards_or_web_fallback(
                    "zero_supabase_rows", rows, filters, facility_type, city, state, descriptive_text
                )

            # Anchor guard (code-level, Stage 5 gate): non-trivial residue but
            # zero structured filters resolved -- do not run an unfiltered
            # semantic search across the whole collection. This is the
            # "fuzzy-only, no anchor -> ask first" rule enforced in code, not
            # left to the system prompt alone. Nothing to build a web query
            # from either, so asking is still correct here too.
            if not filters:
                log_search("  STOP: descriptive text given but zero structured filters resolved -- refusing an unfiltered nationwide search")
                return NOT_ENOUGH_INFO_MESSAGE, None

            # Stage 5 + 6.
            log_search(f"  PATH: full pipeline (embed + Qdrant search)")
            vectors = await embed_texts([descriptive_text])
            log_search(f"  EMBEDDING: model={EMBEDDING_MODEL} | input={descriptive_text!r} | vector_dims={len(vectors[0])}")

            points = await qdrant_index.search_points(vectors[0], query_filter, limit=RESULT_LIMIT)
            log_search(f"  QDRANT SEARCH: collection={qdrant_index.COLLECTION_NAME} | filter={filters} | limit={RESULT_LIMIT} "
                       f"-> {len(points)} point(s) returned")
            for p in points:
                log_search(f"    point facility_id={p.id} score={p.score:.4f}")

            if not points:
                log_search("  RESULT: zero Qdrant matches for this filter -- routing to web fallback")
                return await _cards_or_web_fallback(
                    "zero_qdrant_points", [], filters, facility_type, city, state, descriptive_text
                )

            top_score = points[0].score
            log_search(f"  THRESHOLD CHECK: top_score={top_score:.4f} vs SCORE_FLOOR={SCORE_FLOOR} "
                       f"-> {'PASS (ranked results trusted)' if top_score >= SCORE_FLOOR else 'FAIL (falling back to unranked Supabase list)'}")

            # Stage 7: low confidence -> fall back to a plain structured filter
            # query, NOT the web fallback -- real matches exist here, just not
            # semantically confident ones. Deliberately unchanged from before
            # Phase 8: this is a ranking-confidence issue, not a coverage/
            # data-absence one, so it stays a certified (if unranked) result.
            # filters is guaranteed non-empty here (anchor guard above), so
            # this can never degrade into an unfiltered random list.
            if top_score < SCORE_FLOOR:
                rows = await _supabase_filtered_query(conn, filters)
                log_search(f"  SOURCE: infomary_facilities JOIN infomary_facility_detail (fallback, unranked) -- {len(rows)} row(s) returned")
                cards = [_row_to_card(r, r["facility_type"]) for r in rows]
                return _intro_text(rows, filters, low_confidence=True), cards

            # Stage 8: enrich from Supabase, preserving Qdrant's ranking order.
            facility_ids = [p.id for p in points]
            enriched = await _enrich(conn, facility_ids)
            log_search(f"  SOURCE: infomary_facilities JOIN infomary_facility_detail WHERE facility_id = ANY(...) "
                       f"-- {len(enriched)}/{len(facility_ids)} facility_id(s) matched (Qdrant-ranked order preserved)")
            rows = [enriched[fid] for fid in facility_ids if fid in enriched]
            cards = [_row_to_card(r, r["facility_type"]) for r in rows]
            return _intro_text(rows, filters, low_confidence=False), cards

    except Exception as e:
        log_warn(f"facility_search | unexpected failure | {type(e).__name__}: {e}")
        return "Sorry, I couldn't search facility data right now -- please try again in a moment.", None