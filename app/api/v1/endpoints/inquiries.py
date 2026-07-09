import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import AuthenticatedUser
from app.dependencies import require_user_or_guest
from app.models.facility import Facility
from app.models.inquiry import Inquiry
from app.schemas.inquiry import InquiryCreate, InquiryOut
from app.services.profile_service import ensure_profile_exists

router = APIRouter(prefix="/inquiries", tags=["inquiries"])


@router.post("", response_model=InquiryOut, status_code=status.HTTP_201_CREATED)
async def create_inquiry(
    payload: InquiryCreate,
    user: AuthenticatedUser = Depends(require_user_or_guest),
    db: AsyncSession = Depends(get_db),
):
    try:
        fac_uuid = uuid.UUID(payload.facility_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")

    exists = (await db.execute(select(Facility.id).where(Facility.id == fac_uuid))).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")

    await ensure_profile_exists(db, user)

    inquiry = Inquiry(
        user_id=uuid.UUID(user.user_id),
        facility_id=fac_uuid,
        message=payload.message,
        contact_phone=payload.contact_phone,
        contact_time_preference=payload.contact_time_preference,
    )
    db.add(inquiry)
    await db.commit()
    await db.refresh(inquiry)
    return InquiryOut.model_validate(inquiry)


@router.get("/me", response_model=list[InquiryOut])
async def list_my_inquiries(
    user: AuthenticatedUser = Depends(require_user_or_guest),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Inquiry)
        .where(Inquiry.user_id == uuid.UUID(user.user_id))
        .order_by(Inquiry.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [InquiryOut.model_validate(r) for r in rows]
