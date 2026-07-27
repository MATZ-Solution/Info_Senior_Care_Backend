




# """
# Facility endpoints -- the highest-traffic surface in the app (Home + Search
# screens hit these on every load). Design rules enforced here:

#   1. List-style endpoints (search/suggest/recommended) SELECT ONLY core-table
#      columns -- never join nursing_home_details/home_health_details/
#      facility_services, which are wide and mostly-null. Only the single
#      detail endpoint pays for that join, and only for one relevant table.
#   2. Search results are cached in Valkey for CACHE_TTL_SECONDS, keyed by the
#      exact query parameters, since the same filters get hit repeatedly by
#      many of the (target) 10k concurrent users.
# """
# import hashlib
# import uuid
# from typing import Optional

# from fastapi import APIRouter, Depends, HTTPException, Query, status
# from sqlalchemy import func, or_, select
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy.orm import selectinload

# from app.core.cache import cache_get, cache_set
# from app.core.database import get_db
# from app.core.fuzzy_search import fuzzy_or_exact
# from app.core.security import AuthenticatedUser
# from app.dependencies import optional_user_or_guest
# from app.services.state_normalize import normalize_state
# from app.models.assessment import Assessment
# from app.models.facility import Facility
# from app.models.profile import Profile
# from app.schemas.facility import (
#     FacilityCard,
#     FacilityDetail,
#     FacilitySuggestItem,
#     PaginatedFacilities,
# )

# router = APIRouter(prefix="/facilities", tags=["facilities"])


# def _normalize_type_token(value: str) -> str:
#     """
#     'Nursing Home', 'NURSING_HOME', 'nursing-home', 'nursing_home' all
#     normalize to the same token ('nursinghome'), so search matches
#     regardless of how a given row's facility_type happens to be
#     formatted -- CSV/manual-upload data is rarely 100% consistent, and the
#     frontend will send one fixed value per dropdown option, so the
#     normalization needs to happen on the data side too, not just the input.
#     """
#     return "".join(ch for ch in value.lower() if ch.isalnum())

# # Only these columns are ever loaded for list-style endpoints -- enforced by
# # an explicit `.with_only_columns(...)`-style attribute list via load_only,
# # so adding a column to the ORM model later doesn't silently widen this query.
# _CARD_ATTRS = (
#     Facility.id, Facility.name, Facility.facility_type, Facility.facility_type_category,
#     Facility.city, Facility.state, Facility.zip_code, Facility.latitude, Facility.longitude,
#     Facility.overall_rating, Facility.bed_count, Facility.ownership_type,
# )


# def _cache_key(prefix: str, **params) -> str:
#     raw = "|".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
#     digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
#     return f"{prefix}:{digest}"


# @router.get("/search", response_model=PaginatedFacilities)
# async def search_facilities(
#     state: Optional[str] = Query(default=None, min_length=2, max_length=30),
#     zip_code: Optional[str] = None,
#     city: Optional[str] = None,
#     name: Optional[str] = None,
#     facility_type: Optional[str] = None,
#     facility_type_category: Optional[str] = None,
#     page: int = Query(default=1, ge=1),
#     page_size: int = Query(default=20, ge=1, le=100),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     NOTE: budget_min/budget_max filters are intentionally NOT implemented
#     yet -- the current facility dataset (CMS + state directories) has no
#     pricing field. Wiring these params to an unrelated column (e.g.
#     bed_count) would silently return wrong results, which is worse than not
#     filtering at all. Add a real `price_min`/`price_max` column (and
#     ingestion source for it) before exposing these filters.

#     `facility_type_category` is the STANDARDIZED, small fixed-set field
#     (e.g. "Nursing Home / Skilled Nursing Facility", "Hospice") meant for a
#     hardcoded frontend dropdown -- prefer this over `facility_type` (raw,
#     messy source text with many variants) whenever the frontend offers a
#     fixed set of options rather than free text.

#     All free-text filters (`city`, `name`, `facility_type`,
#     `facility_type_category`) are typo-tolerant: they match either an exact/
#     substring match OR a Postgres trigram-similarity match (see
#     app/core/fuzzy_search.py), so e.g. "nurshing home" still finds rows
#     stored as "Nursing Home". Requires the pg_trgm extension + GIN trigram
#     indexes (see migration bb66caf474c1).
#     """
#     cache_key = _cache_key(
#         "facilities:search", state=state, zip_code=zip_code, city=city, name=name,
#         facility_type=facility_type, facility_type_category=facility_type_category,
#         page=page, page_size=page_size,
#     )
#     cached = await cache_get(cache_key)
#     if cached is not None:
#         return PaginatedFacilities(**cached)

#     conditions = [Facility.is_active.is_(True)]
#     if state:
#         normalized_state = normalize_state(state)
#         if normalized_state:
#             conditions.append(Facility.state == normalized_state)
#     if zip_code:
#         conditions.append(Facility.zip_code == zip_code)
#     if city:
#         conditions.append(fuzzy_or_exact(Facility.city, city))
#     if name:
#         conditions.append(fuzzy_or_exact(Facility.name, name))
#     if facility_type_category:
#         # Normalized exact match (fast path) OR trigram fuzzy match (catches
#         # genuine typos/spelling mistakes the normalization can't fix).
#         conditions.append(
#             or_(
#                 func.lower(func.regexp_replace(Facility.facility_type_category, "[^a-zA-Z0-9]", "", "g"))
#                 == _normalize_type_token(facility_type_category),
#                 func.word_similarity(facility_type_category, Facility.facility_type_category) > 0.4,
#             )
#         )
#     if facility_type:
#         # Same two-tier approach: format-normalized exact match, OR fuzzy
#         # trigram similarity for actual spelling mistakes.
#         conditions.append(
#             or_(
#                 func.lower(func.regexp_replace(Facility.facility_type, "[^a-zA-Z0-9]", "", "g"))
#                 == _normalize_type_token(facility_type),
#                 func.word_similarity(facility_type, Facility.facility_type) > 0.4,
#             )
#         )

#     count_stmt = select(func.count()).select_from(Facility).where(*conditions)
#     total = (await db.execute(count_stmt)).scalar_one()

#     stmt = (
#         select(*_CARD_ATTRS)
#         .where(*conditions)
#         .order_by(Facility.overall_rating.desc().nullslast(), Facility.name)
#         .offset((page - 1) * page_size)
#         .limit(page_size)
#     )
#     rows = (await db.execute(stmt)).all()
#     items = [FacilityCard(**row._mapping) for row in rows]

#     response = PaginatedFacilities(
#         items=items, page=page, page_size=page_size, total=total,
#         has_more=(page * page_size) < total,
#     )
#     await cache_set(cache_key, response.model_dump())
#     return response


# @router.get("/suggest", response_model=list[FacilitySuggestItem])
# async def suggest_facilities(
#     q: str = Query(..., min_length=1, max_length=200),
#     limit: int = Query(default=8, ge=1, le=20),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Autocomplete. Two-tier matching:
#       1. Fast, precise substring match (ILIKE) first -- this handles the
#          overwhelming majority of correctly-typed autocomplete input with
#          no noise.
#       2. ONLY if that finds literally nothing, fall back to typo-tolerant
#          trigram similarity matching (pg_trgm), ordered by similarity score
#          (best match first) -- NOT combined/OR'd into the main query above,
#          because trigram similarity on short autocomplete strings (e.g.
#          "Sun") produces noisy false positives (e.g. matching "1ST
#          SUPPORT...") that would drown out real substring matches if
#          applied unconditionally.
#     """
#     cache_key = _cache_key("facilities:suggest", q=q.lower(), limit=limit)
#     cached = await cache_get(cache_key)
#     if cached is not None:
#         return [FacilitySuggestItem(**item) for item in cached]

#     stmt = (
#         select(Facility.id, Facility.name, Facility.city, Facility.state)
#         .where(Facility.is_active.is_(True), Facility.name.ilike(f"%{q}%"))
#         .order_by(Facility.name)
#         .limit(limit)
#     )
#     rows = (await db.execute(stmt)).all()

#     if not rows:
#         similarity_score = func.word_similarity(q, Facility.name)
#         fuzzy_stmt = (
#             select(Facility.id, Facility.name, Facility.city, Facility.state)
#             .where(Facility.is_active.is_(True), similarity_score > 0.4)
#             .order_by(similarity_score.desc())
#             .limit(limit)
#         )
#         rows = (await db.execute(fuzzy_stmt)).all()

#     items = [FacilitySuggestItem(**row._mapping) for row in rows]
#     await cache_set(cache_key, [item.model_dump() for item in items], ttl_seconds=120)
#     return items


# @router.get("/recommended", response_model=list[FacilityCard])
# async def recommended_facilities(
#     limit: int = Query(default=10, ge=1, le=50),
#     user: Optional[AuthenticatedUser] = Depends(optional_user_or_guest),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Home-screen recommendations. No manual `state`/location param here on
#     purpose -- that belongs on the Search screen, where the person is
#     explicitly choosing what to look for. Home-screen recommendations
#     should just reflect what the person already told us:

#       - If they're logged in/guest AND completed onboarding: scope to their
#         onboarding location's state.
#       - If they also have a completed assessment: additionally scope to
#         that assessment's recommended facility_type_category.
#       - If neither is available (no session, or onboarding/assessment
#         skipped) -- perfectly normal, not an error -- fall back to global
#         highest-rated active facilities, same as before.

#     Explicit state/type filtering by choice still lives on
#     GET /facilities/search.
#     """
#     onboarding_state: Optional[str] = None
#     recommended_care_type: Optional[str] = None

#     if user is not None:
#         try:
#             user_uuid = uuid.UUID(user.user_id)
#         except ValueError:
#             user_uuid = None

#         if user_uuid is not None:
#             profile = (
#                 await db.execute(select(Profile).where(Profile.id == user_uuid))
#             ).scalar_one_or_none()
#             if profile and profile.onboarding_data:
#                 location = profile.onboarding_data.get("location") or {}
#                 onboarding_state = normalize_state(location.get("state"))

#             latest_assessment = (
#                 await db.execute(
#                     select(Assessment)
#                     .where(Assessment.user_id == user_uuid)
#                     .order_by(Assessment.created_at.desc())
#                     .limit(1)
#                 )
#             ).scalar_one_or_none()
#             if latest_assessment:
#                 recommended_care_type = latest_assessment.recommended_care_type

#     cache_key = _cache_key(
#         "facilities:recommended",
#         state=onboarding_state, care_type=recommended_care_type, limit=limit,
#     )
#     cached = await cache_get(cache_key)
#     if cached is not None:
#         return [FacilityCard(**item) for item in cached]

#     conditions = [Facility.is_active.is_(True), Facility.overall_rating.isnot(None)]
#     if onboarding_state:
#         conditions.append(Facility.state == onboarding_state)
#     if recommended_care_type:
#         conditions.append(Facility.facility_type_category == recommended_care_type)

#     stmt = (
#         select(*_CARD_ATTRS)
#         .where(*conditions)
#         .order_by(Facility.overall_rating.desc())
#         .limit(limit)
#     )
#     rows = (await db.execute(stmt)).all()
#     items = [FacilityCard(**row._mapping) for row in rows]

#     # If personalized filters matched nothing (e.g. no facilities of the
#     # recommended type in their state yet), fall back to the generic
#     # top-rated list rather than showing an empty home screen.
#     if not items and (onboarding_state or recommended_care_type):
#         fallback_stmt = (
#             select(*_CARD_ATTRS)
#             .where(Facility.is_active.is_(True), Facility.overall_rating.isnot(None))
#             .order_by(Facility.overall_rating.desc())
#             .limit(limit)
#         )
#         rows = (await db.execute(fallback_stmt)).all()
#         items = [FacilityCard(**row._mapping) for row in rows]

#     await cache_set(cache_key, [item.model_dump() for item in items])
#     return items


# @router.get("/{facility_id}", response_model=FacilityDetail)
# async def get_facility_detail(facility_id: str, db: AsyncSession = Depends(get_db)):
#     try:
#         fac_uuid = uuid.UUID(facility_id)
#     except ValueError:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")

#     cache_key = f"facilities:detail:{facility_id}"
#     cached = await cache_get(cache_key)
#     if cached is not None:
#         return FacilityDetail(**cached)

#     stmt = (
#         select(Facility)
#         .where(Facility.id == fac_uuid, Facility.is_active.is_(True))
#         .options(
#             selectinload(Facility.nursing_home_detail),
#             selectinload(Facility.home_health_detail),
#             selectinload(Facility.services),
#         )
#     )
#     facility = (await db.execute(stmt)).scalar_one_or_none()
#     if facility is None:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")

#     detail = FacilityDetail.model_validate(facility)
#     await cache_set(cache_key, detail.model_dump(mode="json"), ttl_seconds=300)
#     return detail






































# """
# PATCH REFERENCE — app/api/v1/endpoints/facilities.py

# This file is NOT dropped into the project as-is. It contains the exact blocks
# to paste into the existing `app/api/v1/endpoints/facilities.py`, which is
# 38 KB and mostly commented-out previous revisions; replacing the whole file
# wholesale would be a much larger and riskier diff than the change requires.

# THREE EDITS, IN ORDER:
#     EDIT 1 — add imports          (top of file, after the existing imports)
#     EDIT 2 — replace search_facilities   (currently around line 673)
#     EDIT 3 — replace suggest_facilities  (currently around line 768)

# WHAT CHANGES FOR THE CLIENT: nothing. Same path, same query parameters, same
# response model. TypeSense is tried first; if it is disabled, unreachable, or
# the collection does not exist, the request falls through to the existing
# Postgres implementation, which is preserved verbatim and simply moved into a
# private helper.
# """

# # ===========================================================================
# # EDIT 1 — imports
# # ---------------------------------------------------------------------------
# # Add these next to the existing imports at the top of facilities.py.
# # Do not remove anything that is already there: the Postgres path still needs
# # all of it.
# # ===========================================================================

# from typesense.exceptions import ObjectNotFound  # noqa: E402

# from app.core.typesense import TypesenseUnavailable, is_configured  # noqa: E402
# from app.services import typesense_search_service  # noqa: E402


# # ===========================================================================
# # EDIT 2 — replace the whole `search_facilities` function
# # ---------------------------------------------------------------------------
# # Find `@router.get("/search", response_model=PaginatedFacilities)` and replace
# # that decorator plus its function with everything below, up to (not including)
# # the EDIT 3 banner.
# # ===========================================================================


# @router.get("/search", response_model=PaginatedFacilities)
# async def search_facilities(
#     state: Optional[str] = Query(default=None, min_length=2, max_length=30),
#     zip_code: Optional[str] = None,
#     city: Optional[str] = None,
#     name: Optional[str] = None,
#     facility_type: Optional[str] = None,
#     facility_type_category: Optional[str] = None,
#     q: Optional[str] = Query(
#         default=None,
#         max_length=200,
#         description=(
#             "Optional single free-text query searched across name, city, address and "
#             "state at once. Additive and backward compatible — existing clients that "
#             "send city/name separately are unaffected."
#         ),
#     ),
#     page: int = Query(default=1, ge=1),
#     page_size: int = Query(default=20, ge=1, le=100),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Facility search. Served by TypeSense, with Postgres as the fallback.

#     `state` accepts a full name or an abbreviation ("California" or "CA") — it
#     is normalized before it reaches either backend.

#     `facility_type_category` is the STANDARDIZED small-fixed-set field
#     ("Nursing Home / Skilled Nursing Facility", "Hospice") meant for a
#     hardcoded frontend dropdown. Prefer it over `facility_type`, which is raw
#     source text with many variants.

#     NOTE: budget_min/budget_max remain intentionally unimplemented — the
#     dataset has no pricing column, and wiring them to an unrelated field would
#     silently return wrong results.
#     """
#     cache_key = _cache_key(
#         "facilities:search",
#         state=state, zip_code=zip_code, city=city, name=name, q=q,
#         facility_type=facility_type, facility_type_category=facility_type_category,
#         page=page, page_size=page_size,
#     )
#     cached = await cache_get(cache_key)
#     if cached is not None:
#         return PaginatedFacilities(**cached)

#     # Normalize once, here, so both backends filter on the same value and the
#     # cache key above stays consistent with what was actually queried.
#     normalized_state = normalize_state(state) if state else None

#     response: PaginatedFacilities | None = None

#     if is_configured():
#         try:
#             result = await typesense_search_service.search_facilities(
#                 q=q,
#                 name=name,
#                 city=city,
#                 state=normalized_state,
#                 zip_code=zip_code,
#                 facility_type=facility_type,
#                 facility_type_category=facility_type_category,
#                 page=page,
#                 page_size=page_size,
#             )
#             response = PaginatedFacilities(**result)
#         except ObjectNotFound:
#             # The collection has not been created/imported yet. Expected on a
#             # fresh environment; log at warning so it is visible but does not
#             # page anyone.
#             logger.warning(
#                 "TypeSense collection missing — falling back to Postgres. "
#                 "Run: python -m scripts.typesense_import --full"
#             )
#         except TypesenseUnavailable as exc:
#             # Search must survive the search engine being down. Postgres is
#             # the source of truth and can still answer.
#             logger.warning("TypeSense unavailable, falling back to Postgres: %s", exc)

#     if response is None:
#         response = await _search_facilities_postgres(
#             db=db,
#             state=normalized_state,
#             zip_code=zip_code,
#             city=city,
#             name=name or q,  # `q` degrades to a name search on the fallback path
#             facility_type=facility_type,
#             facility_type_category=facility_type_category,
#             page=page,
#             page_size=page_size,
#         )

#     await cache_set(cache_key, response.model_dump())
#     return response


# async def _search_facilities_postgres(
#     *,
#     db: AsyncSession,
#     state: Optional[str],
#     zip_code: Optional[str],
#     city: Optional[str],
#     name: Optional[str],
#     facility_type: Optional[str],
#     facility_type_category: Optional[str],
#     page: int,
#     page_size: int,
# ) -> PaginatedFacilities:
#     """
#     The original Postgres implementation, unchanged in behaviour.

#     Kept rather than deleted for two reasons: it is the fallback when TypeSense
#     is unavailable, and it is the reference for verifying that the TypeSense
#     path returns equivalent results. Do not delete it after the migration
#     "settles" — a search engine that has no fallback is a single point of
#     failure for the app's primary screen.

#     `state` arrives already normalized. Caching happens in the caller.
#     """
#     conditions = [Facility.is_active.is_(True)]
#     if state:
#         conditions.append(Facility.state == state)
#     if zip_code:
#         conditions.append(Facility.zip_code == zip_code)
#     if city:
#         conditions.append(fuzzy_or_exact(Facility.city, city))
#     if name:
#         conditions.append(fuzzy_or_exact(Facility.name, name))
#     if facility_type_category:
#         conditions.append(
#             or_(
#                 func.lower(
#                     func.regexp_replace(Facility.facility_type_category, "[^a-zA-Z0-9]", "", "g")
#                 )
#                 == _normalize_type_token(facility_type_category),
#                 func.word_similarity(facility_type_category, Facility.facility_type_category) > 0.4,
#             )
#         )
#     if facility_type:
#         conditions.append(
#             or_(
#                 func.lower(func.regexp_replace(Facility.facility_type, "[^a-zA-Z0-9]", "", "g"))
#                 == _normalize_type_token(facility_type),
#                 func.word_similarity(facility_type, Facility.facility_type) > 0.4,
#             )
#         )

#     count_stmt = select(func.count()).select_from(Facility).where(*conditions)
#     total = (await db.execute(count_stmt)).scalar_one()

#     stmt = (
#         select(*_CARD_ATTRS)
#         .where(*conditions)
#         .order_by(Facility.overall_rating.desc().nullslast(), Facility.name)
#         .offset((page - 1) * page_size)
#         .limit(page_size)
#     )
#     rows = (await db.execute(stmt)).all()
#     items = [FacilityCard(**row._mapping) for row in rows]

#     return PaginatedFacilities(
#         items=items,
#         page=page,
#         page_size=page_size,
#         total=total,
#         has_more=(page * page_size) < total,
#     )


# # ===========================================================================
# # EDIT 3 — replace the whole `suggest_facilities` function
# # ---------------------------------------------------------------------------
# # Same pattern: TypeSense first, existing two-tier Postgres query as fallback.
# # ===========================================================================


# @router.get("/suggest", response_model=list[FacilitySuggestItem])
# async def suggest_facilities(
#     q: str = Query(..., min_length=1, max_length=200),
#     limit: int = Query(default=8, ge=1, le=20),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Autocomplete for the search box.

#     TypeSense handles prefix, infix and single-typo matching natively, which
#     replaces the old two-tier ILIKE-then-trigram approach with one query. The
#     Postgres version remains as the fallback.
#     """
#     cache_key = _cache_key("facilities:suggest", q=q.lower(), limit=limit)
#     cached = await cache_get(cache_key)
#     if cached is not None:
#         return [FacilitySuggestItem(**item) for item in cached]

#     items: list[FacilitySuggestItem] | None = None

#     if is_configured():
#         try:
#             results = await typesense_search_service.suggest_facilities(q, limit)
#             items = [FacilitySuggestItem(**item) for item in results]
#         except ObjectNotFound:
#             logger.warning("TypeSense collection missing — suggest falling back to Postgres")
#         except TypesenseUnavailable as exc:
#             logger.warning("TypeSense unavailable, suggest falling back to Postgres: %s", exc)

#     if items is None:
#         items = await _suggest_facilities_postgres(db=db, q=q, limit=limit)

#     await cache_set(cache_key, [item.model_dump() for item in items], ttl_seconds=120)
#     return items


# async def _suggest_facilities_postgres(
#     *, db: AsyncSession, q: str, limit: int
# ) -> list[FacilitySuggestItem]:
#     """
#     Original two-tier autocomplete, unchanged.

#     1. Fast, precise substring (ILIKE) — handles correctly-typed input with no
#        noise.
#     2. Only if that returns nothing, trigram similarity. Not OR'd into the
#        query above: on short strings like "Sun", trigram similarity produces
#        false positives ("1ST SUPPORT...") that would drown out real matches.
#     """
#     stmt = (
#         select(Facility.id, Facility.name, Facility.city, Facility.state)
#         .where(Facility.is_active.is_(True), Facility.name.ilike(f"%{q}%"))
#         .order_by(Facility.name)
#         .limit(limit)
#     )
#     rows = (await db.execute(stmt)).all()

#     if not rows:
#         similarity_score = func.word_similarity(q, Facility.name)
#         fuzzy_stmt = (
#             select(Facility.id, Facility.name, Facility.city, Facility.state)
#             .where(Facility.is_active.is_(True), similarity_score > 0.4)
#             .order_by(similarity_score.desc())
#             .limit(limit)
#         )
#         rows = (await db.execute(fuzzy_stmt)).all()

#     return [FacilitySuggestItem(**row._mapping) for row in rows]


# # ===========================================================================
# # IF `logger` IS NOT ALREADY DEFINED IN facilities.py
# # ---------------------------------------------------------------------------
# # Add near the top, under the imports:
# #
# #     import logging
# #     logger = logging.getLogger("app.api.facilities")
# # ===========================================================================













"""
Facility endpoints -- the highest-traffic surface in the app (Home + Search
screens hit these on every load). Design rules enforced here:

  1. List-style endpoints (search/suggest/recommended) SELECT ONLY core-table
     columns -- never join nursing_home_details/home_health_details/
     facility_services, which are wide and mostly-null. Only the single
     detail endpoint pays for that join, and only for one relevant table.
  2. Search results are cached in Valkey for CACHE_TTL_SECONDS, keyed by the
     exact query parameters, since the same filters get hit repeatedly by
     many of the (target) 10k concurrent users.
  3. /search and /suggest are served by TypeSense, with the original Postgres
     implementations kept as the fallback path. TypeSense is a derived index;
     Postgres remains the source of truth, so the search engine being down
     must never take search down. See _search_facilities_postgres below.
"""
import hashlib
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typesense.exceptions import ObjectNotFound

from app.core.cache import cache_get, cache_set
from app.core.database import get_db
from app.core.fuzzy_search import fuzzy_or_exact
from app.core.security import AuthenticatedUser
from app.core.typesense import TypesenseUnavailable, is_configured
from app.dependencies import optional_user_or_guest
from app.models.assessment import Assessment
from app.models.facility import Facility
from app.models.profile import Profile
from app.schemas.facility import (
    FacilityCard,
    FacilityDetail,
    FacilitySuggestItem,
    PaginatedFacilities,
)
from app.services import typesense_search_service
from app.services.state_normalize import normalize_state

logger = logging.getLogger("app.api.facilities")

router = APIRouter(prefix="/facilities", tags=["facilities"])


def _normalize_type_token(value: str) -> str:
    """
    'Nursing Home', 'NURSING_HOME', 'nursing-home', 'nursing_home' all
    normalize to the same token ('nursinghome'), so search matches
    regardless of how a given row's facility_type happens to be
    formatted -- CSV/manual-upload data is rarely 100% consistent, and the
    frontend will send one fixed value per dropdown option, so the
    normalization needs to happen on the data side too, not just the input.
    """
    return "".join(ch for ch in value.lower() if ch.isalnum())


# Only these columns are ever loaded for list-style endpoints, so adding a
# column to the ORM model later doesn't silently widen these queries.
_CARD_ATTRS = (
    Facility.id, Facility.name, Facility.facility_type, Facility.facility_type_category,
    Facility.city, Facility.state, Facility.zip_code, Facility.latitude, Facility.longitude,
    Facility.overall_rating, Facility.bed_count, Facility.ownership_type,
)


def _cache_key(prefix: str, **params) -> str:
    raw = "|".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@router.get("/search", response_model=PaginatedFacilities)
async def search_facilities(
    state: Optional[str] = Query(default=None, min_length=2, max_length=30),
    zip_code: Optional[str] = None,
    city: Optional[str] = None,
    name: Optional[str] = None,
    facility_type: Optional[str] = None,
    facility_type_category: Optional[str] = None,
    q: Optional[str] = Query(
        default=None,
        max_length=200,
        description=(
            "Optional single free-text query searched across name, city, address, "
            "state and zip code at once. Additive and backward compatible -- "
            "existing clients that send city/name separately are unaffected."
        ),
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Facility search. Served by TypeSense, with Postgres as the fallback.

    `state` accepts a full name or an abbreviation ("California" or "CA") --
    it is normalized before it reaches either backend.

    `facility_type_category` is the STANDARDIZED, small fixed-set field
    (e.g. "Nursing Home / Skilled Nursing Facility", "Hospice") meant for a
    hardcoded frontend dropdown -- prefer this over `facility_type` (raw,
    messy source text with many variants) whenever the frontend offers a
    fixed set of options rather than free text.

    Typo tolerance is per field: name and city allow two edits, address one,
    and state/zip zero. "CA" and "GA" are one edit apart and are different
    real places, so a fuzzy match there would return confidently wrong
    results.

    NOTE: budget_min/budget_max filters are intentionally NOT implemented
    yet -- the current facility dataset (CMS + state directories) has no
    pricing field. Wiring these params to an unrelated column (e.g.
    bed_count) would silently return wrong results, which is worse than not
    filtering at all.
    """
    cache_key = _cache_key(
        "facilities:search", state=state, zip_code=zip_code, city=city, name=name, q=q,
        facility_type=facility_type, facility_type_category=facility_type_category,
        page=page, page_size=page_size,
    )
    cached = await cache_get(cache_key)
    if cached is not None:
        return PaginatedFacilities(**cached)

    # Normalize once, here, so both backends filter on the same value.
    normalized_state = normalize_state(state) if state else None

    response: Optional[PaginatedFacilities] = None

    if is_configured():
        try:
            result = await typesense_search_service.search_facilities(
                q=q,
                name=name,
                city=city,
                state=normalized_state,
                zip_code=zip_code,
                facility_type=facility_type,
                facility_type_category=facility_type_category,
                page=page,
                page_size=page_size,
            )
            response = PaginatedFacilities(**result)
        except ObjectNotFound:
            # Collection not created/imported yet. Expected on a fresh
            # environment; visible in logs but not worth paging anyone over.
            logger.warning(
                "TypeSense collection missing -- falling back to Postgres. "
                "Run: python -m scripts.typesense_import --full"
            )
        except TypesenseUnavailable as exc:
            # Search must survive the search engine being down.
            logger.warning("TypeSense unavailable, falling back to Postgres: %s", exc)

    if response is None:
        response = await _search_facilities_postgres(
            db=db,
            state=normalized_state,
            zip_code=zip_code,
            city=city,
            name=name or q,  # `q` degrades to a name search on the fallback path
            facility_type=facility_type,
            facility_type_category=facility_type_category,
            page=page,
            page_size=page_size,
        )

    await cache_set(cache_key, response.model_dump())
    return response


async def _search_facilities_postgres(
    *,
    db: AsyncSession,
    state: Optional[str],
    zip_code: Optional[str],
    city: Optional[str],
    name: Optional[str],
    facility_type: Optional[str],
    facility_type_category: Optional[str],
    page: int,
    page_size: int,
) -> PaginatedFacilities:
    """
    The original Postgres implementation, unchanged in behaviour.

    Kept rather than deleted for two reasons: it is the fallback when
    TypeSense is unavailable, and it is the reference for verifying that the
    TypeSense path returns equivalent results. Do NOT delete it once the
    migration settles -- a search engine with no fallback is a single point of
    failure on the app's primary screen.

    All free-text filters here are typo-tolerant via Postgres trigram
    similarity (see app/core/fuzzy_search.py), so e.g. "nurshing home" still
    finds rows stored as "Nursing Home". Requires the pg_trgm extension + GIN
    trigram indexes (migration bb66caf474c1).

    `state` arrives already normalized. Caching happens in the caller.
    """
    conditions = [Facility.is_active.is_(True)]
    if state:
        conditions.append(Facility.state == state)
    if zip_code:
        conditions.append(Facility.zip_code == zip_code)
    if city:
        conditions.append(fuzzy_or_exact(Facility.city, city))
    if name:
        conditions.append(fuzzy_or_exact(Facility.name, name))
    if facility_type_category:
        conditions.append(
            or_(
                func.lower(func.regexp_replace(Facility.facility_type_category, "[^a-zA-Z0-9]", "", "g"))
                == _normalize_type_token(facility_type_category),
                func.word_similarity(facility_type_category, Facility.facility_type_category) > 0.4,
            )
        )
    if facility_type:
        conditions.append(
            or_(
                func.lower(func.regexp_replace(Facility.facility_type, "[^a-zA-Z0-9]", "", "g"))
                == _normalize_type_token(facility_type),
                func.word_similarity(facility_type, Facility.facility_type) > 0.4,
            )
        )

    count_stmt = select(func.count()).select_from(Facility).where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(*_CARD_ATTRS)
        .where(*conditions)
        .order_by(Facility.overall_rating.desc().nullslast(), Facility.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).all()
    items = [FacilityCard(**row._mapping) for row in rows]

    return PaginatedFacilities(
        items=items, page=page, page_size=page_size, total=total,
        has_more=(page * page_size) < total,
    )


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------


@router.get("/suggest", response_model=list[FacilitySuggestItem])
async def suggest_facilities(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """
    Autocomplete for the search box.

    TypeSense handles prefix, infix and single-typo matching natively, which
    replaces the old two-tier ILIKE-then-trigram approach with one query. The
    Postgres version remains as the fallback.
    """
    cache_key = _cache_key("facilities:suggest", q=q.lower(), limit=limit)
    cached = await cache_get(cache_key)
    if cached is not None:
        return [FacilitySuggestItem(**item) for item in cached]

    items: Optional[list[FacilitySuggestItem]] = None

    if is_configured():
        try:
            results = await typesense_search_service.suggest_facilities(q, limit)
            items = [FacilitySuggestItem(**item) for item in results]
        except ObjectNotFound:
            logger.warning("TypeSense collection missing -- suggest falling back to Postgres")
        except TypesenseUnavailable as exc:
            logger.warning("TypeSense unavailable, suggest falling back to Postgres: %s", exc)

    if items is None:
        items = await _suggest_facilities_postgres(db=db, q=q, limit=limit)

    await cache_set(cache_key, [item.model_dump() for item in items], ttl_seconds=120)
    return items


async def _suggest_facilities_postgres(
    *, db: AsyncSession, q: str, limit: int
) -> list[FacilitySuggestItem]:
    """
    Original two-tier autocomplete, unchanged.

      1. Fast, precise substring match (ILIKE) first -- this handles the
         overwhelming majority of correctly-typed autocomplete input with
         no noise.
      2. ONLY if that finds literally nothing, fall back to typo-tolerant
         trigram similarity matching (pg_trgm), ordered by similarity score
         (best match first) -- NOT combined/OR'd into the main query above,
         because trigram similarity on short autocomplete strings (e.g.
         "Sun") produces noisy false positives (e.g. matching "1ST
         SUPPORT...") that would drown out real substring matches if
         applied unconditionally.
    """
    stmt = (
        select(Facility.id, Facility.name, Facility.city, Facility.state)
        .where(Facility.is_active.is_(True), Facility.name.ilike(f"%{q}%"))
        .order_by(Facility.name)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    if not rows:
        similarity_score = func.word_similarity(q, Facility.name)
        fuzzy_stmt = (
            select(Facility.id, Facility.name, Facility.city, Facility.state)
            .where(Facility.is_active.is_(True), similarity_score > 0.4)
            .order_by(similarity_score.desc())
            .limit(limit)
        )
        rows = (await db.execute(fuzzy_stmt)).all()

    return [FacilitySuggestItem(**row._mapping) for row in rows]


# ---------------------------------------------------------------------------
# Recommendations (unchanged -- Postgres only)
# ---------------------------------------------------------------------------


@router.get("/recommended", response_model=list[FacilityCard])
async def recommended_facilities(
    limit: int = Query(default=10, ge=1, le=50),
    user: Optional[AuthenticatedUser] = Depends(optional_user_or_guest),
    db: AsyncSession = Depends(get_db),
):
    """
    Home-screen recommendations. No manual `state`/location param here on
    purpose -- that belongs on the Search screen, where the person is
    explicitly choosing what to look for. Home-screen recommendations
    should just reflect what the person already told us:

      - If they're logged in/guest AND completed onboarding: scope to their
        onboarding location's state.
      - If they also have a completed assessment: additionally scope to
        that assessment's recommended facility_type_category.
      - If neither is available (no session, or onboarding/assessment
        skipped) -- perfectly normal, not an error -- fall back to global
        highest-rated active facilities, same as before.

    Explicit state/type filtering by choice still lives on
    GET /facilities/search.

    Deliberately NOT moved to TypeSense: this is a filtered top-N over
    structured columns with no text query, which Postgres already answers
    from an index. Routing it through a search engine would add a network
    hop for no gain.
    """
    onboarding_state: Optional[str] = None
    recommended_care_type: Optional[str] = None

    if user is not None:
        try:
            user_uuid = uuid.UUID(user.user_id)
        except ValueError:
            user_uuid = None

        if user_uuid is not None:
            profile = (
                await db.execute(select(Profile).where(Profile.id == user_uuid))
            ).scalar_one_or_none()
            if profile and profile.onboarding_data:
                location = profile.onboarding_data.get("location") or {}
                onboarding_state = normalize_state(location.get("state"))

            latest_assessment = (
                await db.execute(
                    select(Assessment)
                    .where(Assessment.user_id == user_uuid)
                    .order_by(Assessment.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest_assessment:
                recommended_care_type = latest_assessment.recommended_care_type

    cache_key = _cache_key(
        "facilities:recommended",
        state=onboarding_state, care_type=recommended_care_type, limit=limit,
    )
    cached = await cache_get(cache_key)
    if cached is not None:
        return [FacilityCard(**item) for item in cached]

    conditions = [Facility.is_active.is_(True), Facility.overall_rating.isnot(None)]
    if onboarding_state:
        conditions.append(Facility.state == onboarding_state)
    if recommended_care_type:
        conditions.append(Facility.facility_type_category == recommended_care_type)

    stmt = (
        select(*_CARD_ATTRS)
        .where(*conditions)
        .order_by(Facility.overall_rating.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    items = [FacilityCard(**row._mapping) for row in rows]

    if not items and (onboarding_state or recommended_care_type):
        fallback_stmt = (
            select(*_CARD_ATTRS)
            .where(Facility.is_active.is_(True), Facility.overall_rating.isnot(None))
            .order_by(Facility.overall_rating.desc())
            .limit(limit)
        )
        rows = (await db.execute(fallback_stmt)).all()
        items = [FacilityCard(**row._mapping) for row in rows]

    await cache_set(cache_key, [item.model_dump() for item in items])
    return items


# ---------------------------------------------------------------------------
# Detail (unchanged -- Postgres only)
# ---------------------------------------------------------------------------


@router.get("/{facility_id}", response_model=FacilityDetail)
async def get_facility_detail(facility_id: str, db: AsyncSession = Depends(get_db)):
    """
    Full facility record, including the one relevant wide detail table.

    Stays on Postgres: the search index deliberately carries only the fields
    the result card needs, and this endpoint returns everything.

    IMPORTANT -- this route is declared LAST on purpose. "/{facility_id}" is a
    catch-all; if it were declared above /search, /suggest or /recommended,
    FastAPI would match those literal paths against it first and every one of
    them would 400 with "Invalid facility id".
    """
    try:
        fac_uuid = uuid.UUID(facility_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")

    cache_key = f"facilities:detail:{facility_id}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return FacilityDetail(**cached)

    stmt = (
        select(Facility)
        .where(Facility.id == fac_uuid, Facility.is_active.is_(True))
        .options(
            selectinload(Facility.nursing_home_detail),
            selectinload(Facility.home_health_detail),
            selectinload(Facility.services),
        )
    )
    facility = (await db.execute(stmt)).scalar_one_or_none()
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")

    detail = FacilityDetail.model_validate(facility)
    await cache_set(cache_key, detail.model_dump(mode="json"), ttl_seconds=300)
    return detail