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
# from sqlalchemy import func, select
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy.orm import selectinload

# from app.core.cache import cache_get, cache_set
# from app.core.database import get_db
# from app.models.facility import Facility
# from app.schemas.facility import (
#     FacilityCard,
#     FacilityDetail,
#     FacilitySuggestItem,
#     PaginatedFacilities,
# )

# router = APIRouter(prefix="/facilities", tags=["facilities"])

# # Only these columns are ever loaded for list-style endpoints -- enforced by
# # an explicit `.with_only_columns(...)`-style attribute list via load_only,
# # so adding a column to the ORM model later doesn't silently widen this query.
# _CARD_ATTRS = (
#     Facility.id, Facility.name, Facility.facility_type, Facility.city,
#     Facility.state, Facility.zip_code, Facility.latitude, Facility.longitude,
#     Facility.overall_rating, Facility.bed_count, Facility.ownership_type,
# )


# def _cache_key(prefix: str, **params) -> str:
#     raw = "|".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
#     digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
#     return f"{prefix}:{digest}"


# @router.get("/search", response_model=PaginatedFacilities)
# async def search_facilities(
#     state: Optional[str] = Query(default=None, min_length=2, max_length=2),
#     zip_code: Optional[str] = None,
#     city: Optional[str] = None,
#     facility_type: Optional[str] = None,
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
#     """
#     cache_key = _cache_key(
#         "facilities:search", state=state, zip_code=zip_code, city=city,
#         facility_type=facility_type, page=page, page_size=page_size,
#     )
#     cached = await cache_get(cache_key)
#     if cached is not None:
#         return PaginatedFacilities(**cached)

#     conditions = [Facility.is_active.is_(True)]
#     if state:
#         conditions.append(Facility.state == state.upper())
#     if zip_code:
#         conditions.append(Facility.zip_code == zip_code)
#     if city:
#         conditions.append(Facility.city.ilike(f"%{city}%"))
#     if facility_type:
#         conditions.append(Facility.facility_type == facility_type)

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
#     items = [FacilitySuggestItem(**row._mapping) for row in rows]
#     await cache_set(cache_key, [item.model_dump() for item in items], ttl_seconds=120)
#     return items


# @router.get("/recommended", response_model=list[FacilityCard])
# async def recommended_facilities(
#     state: Optional[str] = Query(default=None, min_length=2, max_length=2),
#     limit: int = Query(default=10, ge=1, le=50),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Simple v1: highest-rated active facilities, optionally scoped to a
#     state (from the user's onboarding location). Swap in a real
#     personalization model later without changing the response contract.
#     """
#     cache_key = _cache_key("facilities:recommended", state=state, limit=limit)
#     cached = await cache_get(cache_key)
#     if cached is not None:
#         return [FacilityCard(**item) for item in cached]

#     conditions = [Facility.is_active.is_(True), Facility.overall_rating.isnot(None)]
#     if state:
#         conditions.append(Facility.state == state.upper())

#     stmt = (
#         select(*_CARD_ATTRS)
#         .where(*conditions)
#         .order_by(Facility.overall_rating.desc())
#         .limit(limit)
#     )
#     rows = (await db.execute(stmt)).all()
#     items = [FacilityCard(**row._mapping) for row in rows]
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
"""
import hashlib
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_get, cache_set
from app.core.database import get_db
from app.models.facility import Facility
from app.schemas.facility import (
    FacilityCard,
    FacilityDetail,
    FacilitySuggestItem,
    PaginatedFacilities,
)
from app.services.state_normalize import normalize_state

router = APIRouter(prefix="/facilities", tags=["facilities"])

# Only these columns are ever loaded for list-style endpoints -- enforced by
# an explicit `.with_only_columns(...)`-style attribute list via load_only,
# so adding a column to the ORM model later doesn't silently widen this query.
#
# NOTE: `facility_category` (see app/services/facility_category.py and
# app/models/facility.py) already exists in the DB and is populated on
# import, but is deliberately NOT wired into filtering yet -- the
# facility_type cleanup (74 raw source variants -> one clean value) was
# deferred by product decision. Revisit when ready.
_CARD_ATTRS = (
    Facility.id, Facility.name, Facility.facility_type, Facility.city,
    Facility.state, Facility.zip_code, Facility.latitude, Facility.longitude,
    Facility.overall_rating, Facility.bed_count, Facility.ownership_type,
)


def _cache_key(prefix: str, **params) -> str:
    raw = "|".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


@router.get("/search", response_model=PaginatedFacilities)
async def search_facilities(
    state: Optional[str] = Query(default=None, max_length=100),
    zip_code: Optional[str] = None,
    city: Optional[str] = None,
    facility_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    NOTE: budget_min/budget_max filters are intentionally NOT implemented
    yet -- the current facility dataset (CMS + state directories) has no
    pricing field. Wiring these params to an unrelated column (e.g.
    bed_count) would silently return wrong results, which is worse than not
    filtering at all. Add a real `price_min`/`price_max` column (and
    ingestion source for it) before exposing these filters.

    `state` accepts either the full name ("California") or the 2-letter
    abbreviation ("CA") -- the frontend can pass whatever the user typed,
    normalization happens here automatically.

    `facility_type` filtering is exact-match against the raw column for
    now (74 raw source variants, cleanup deferred -- see _CARD_ATTRS note
    above).
    """
    normalized_state = normalize_state(state)
    # Cache key must distinguish "no state filter" (None) from "user typed
    # an unrecognized state" (also normalizes to None) -- otherwise an
    # invalid state search can return a stale/unrelated cached result from
    # a genuinely unfiltered search that happened to run first.
    state_cache_component = "__invalid__" if (state and normalized_state is None) else normalized_state

    cache_key = _cache_key(
        "facilities:search", state=state_cache_component, zip_code=zip_code, city=city,
        facility_type=facility_type, page=page, page_size=page_size,
    )
    cached = await cache_get(cache_key)
    if cached is not None:
        return PaginatedFacilities(**cached)

    conditions = [Facility.is_active.is_(True)]
    if state and normalized_state is None:
        # User typed a state we don't recognize -- return empty results
        # rather than silently ignoring the filter and showing unrelated
        # facilities from every state.
        conditions.append(Facility.state == "__no_match__")
    elif normalized_state:
        conditions.append(Facility.state == normalized_state)
    if zip_code:
        conditions.append(Facility.zip_code == zip_code)
    if city:
        conditions.append(Facility.city.ilike(f"%{city}%"))
    if facility_type:
        conditions.append(Facility.facility_type == facility_type)

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

    response = PaginatedFacilities(
        items=items, page=page, page_size=page_size, total=total,
        has_more=(page * page_size) < total,
    )
    await cache_set(cache_key, response.model_dump())
    return response


@router.get("/suggest", response_model=list[FacilitySuggestItem])
async def suggest_facilities(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    cache_key = _cache_key("facilities:suggest", q=q.lower(), limit=limit)
    cached = await cache_get(cache_key)
    if cached is not None:
        return [FacilitySuggestItem(**item) for item in cached]

    stmt = (
        select(Facility.id, Facility.name, Facility.city, Facility.state)
        .where(Facility.is_active.is_(True), Facility.name.ilike(f"%{q}%"))
        .order_by(Facility.name)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    items = [FacilitySuggestItem(**row._mapping) for row in rows]
    await cache_set(cache_key, [item.model_dump() for item in items], ttl_seconds=120)
    return items


@router.get("/recommended", response_model=list[FacilityCard])
async def recommended_facilities(
    state: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Simple v1: highest-rated active facilities, optionally scoped to a
    state (from the user's onboarding location). Swap in a real
    personalization model later without changing the response contract.
    """
    normalized_state = normalize_state(state)
    state_cache_component = "__invalid__" if (state and normalized_state is None) else normalized_state
    cache_key = _cache_key("facilities:recommended", state=state_cache_component, limit=limit)
    cached = await cache_get(cache_key)
    if cached is not None:
        return [FacilityCard(**item) for item in cached]

    conditions = [Facility.is_active.is_(True), Facility.overall_rating.isnot(None)]
    if state and normalized_state is None:
        conditions.append(Facility.state == "__no_match__")
    elif normalized_state:
        conditions.append(Facility.state == normalized_state)

    stmt = (
        select(*_CARD_ATTRS)
        .where(*conditions)
        .order_by(Facility.overall_rating.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    items = [FacilityCard(**row._mapping) for row in rows]
    await cache_set(cache_key, [item.model_dump() for item in items])
    return items


@router.get("/{facility_id}", response_model=FacilityDetail)
async def get_facility_detail(facility_id: str, db: AsyncSession = Depends(get_db)):
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
