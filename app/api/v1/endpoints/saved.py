# import uuid

# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy import delete, select
# from sqlalchemy.dialects.postgresql import insert as pg_insert
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.core.database import get_db
# from app.core.security import AuthenticatedUser
# from app.dependencies import require_user_or_guest
# from app.models.facility import Facility
# from app.models.saved_facility import SavedFacility
# from app.schemas.common import MessageResponse
# from app.schemas.facility import FacilityCard
# from app.services.profile_service import ensure_profile_exists

# router = APIRouter(prefix="/saved", tags=["saved"])

# _CARD_ATTRS = (
#     Facility.id, Facility.name, Facility.facility_type, Facility.city,
#     Facility.state, Facility.zip_code, Facility.latitude, Facility.longitude,
#     Facility.overall_rating, Facility.bed_count, Facility.ownership_type,
# )


# @router.get("", response_model=list[FacilityCard])
# async def list_saved(
#     user: AuthenticatedUser = Depends(require_user_or_guest),
#     db: AsyncSession = Depends(get_db),
# ):
#     stmt = (
#         select(*_CARD_ATTRS)
#         .join(SavedFacility, SavedFacility.facility_id == Facility.id)
#         .where(SavedFacility.user_id == uuid.UUID(user.user_id))
#         .order_by(SavedFacility.created_at.desc())
#     )
#     rows = (await db.execute(stmt)).all()
#     return [FacilityCard(**row._mapping) for row in rows]


# @router.post("/{facility_id}", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
# async def save_facility(
#     facility_id: str,
#     user: AuthenticatedUser = Depends(require_user_or_guest),
#     db: AsyncSession = Depends(get_db),
# ):
#     try:
#         fac_uuid = uuid.UUID(facility_id)
#     except ValueError:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")

#     exists = (await db.execute(select(Facility.id).where(Facility.id == fac_uuid))).scalar_one_or_none()
#     if exists is None:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")

#     await ensure_profile_exists(db, user)

#     # Idempotent: saving an already-saved facility is a no-op, not an error
#     # (a user double-tapping "save" shouldn't see a failure).
#     stmt = (
#         pg_insert(SavedFacility)
#         .values(user_id=uuid.UUID(user.user_id), facility_id=fac_uuid)
#         .on_conflict_do_nothing(index_elements=["user_id", "facility_id"])
#     )
#     await db.execute(stmt)
#     await db.commit()
#     return MessageResponse(message="Facility saved")


# @router.delete("/{facility_id}", response_model=MessageResponse)
# async def unsave_facility(
#     facility_id: str,
#     user: AuthenticatedUser = Depends(require_user_or_guest),
#     db: AsyncSession = Depends(get_db),
# ):
#     try:
#         fac_uuid = uuid.UUID(facility_id)
#     except ValueError:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")

#     await db.execute(
#         delete(SavedFacility).where(
#             SavedFacility.user_id == uuid.UUID(user.user_id),
#             SavedFacility.facility_id == fac_uuid,
#         )
#     )
#     await db.commit()
#     return MessageResponse(message="Facility removed from saved")








import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import AuthenticatedUser
from app.dependencies import require_user
from app.models.facility import Facility
from app.models.saved_facility import SavedFacility
from app.schemas.common import MessageResponse
from app.schemas.facility import FacilityCard
from app.services.profile_service import ensure_profile_exists

router = APIRouter(prefix="/saved", tags=["saved"])

_CARD_ATTRS = (
    Facility.id, Facility.name, Facility.facility_type, Facility.city,
    Facility.state, Facility.zip_code, Facility.latitude, Facility.longitude,
    Facility.overall_rating, Facility.bed_count, Facility.ownership_type,
)


@router.get("", response_model=list[FacilityCard])
async def list_saved(
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(*_CARD_ATTRS)
        .join(SavedFacility, SavedFacility.facility_id == Facility.id)
        .where(SavedFacility.user_id == uuid.UUID(user.user_id))
        .order_by(SavedFacility.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [FacilityCard(**row._mapping) for row in rows]


@router.post("/{facility_id}", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def save_facility(
    facility_id: str,
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        fac_uuid = uuid.UUID(facility_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")

    exists = (await db.execute(select(Facility.id).where(Facility.id == fac_uuid))).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")

    await ensure_profile_exists(db, user)

    # Idempotent: saving an already-saved facility is a no-op, not an error
    # (a user double-tapping "save" shouldn't see a failure).
    stmt = (
        pg_insert(SavedFacility)
        .values(user_id=uuid.UUID(user.user_id), facility_id=fac_uuid)
        .on_conflict_do_nothing(index_elements=["user_id", "facility_id"])
    )
    await db.execute(stmt)
    await db.commit()
    return MessageResponse(message="Facility saved")


@router.delete("/{facility_id}", response_model=MessageResponse)
async def unsave_facility(
    facility_id: str,
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        fac_uuid = uuid.UUID(facility_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")

    await db.execute(
        delete(SavedFacility).where(
            SavedFacility.user_id == uuid.UUID(user.user_id),
            SavedFacility.facility_id == fac_uuid,
        )
    )
    await db.commit()
    return MessageResponse(message="Facility removed from saved")