# import uuid
 
# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession
 
# from app.core.database import get_db
# from app.core.security import AuthenticatedUser
# from app.dependencies import require_user
# from app.models.facility import Facility
# from app.models.inquiry import Inquiry
# from app.schemas.inquiry import InquiryCreate, InquiryOut
# from app.services.profile_service import ensure_profile_exists
 
# router = APIRouter(prefix="/inquiries", tags=["inquiries"])
 
 
# @router.post("", response_model=InquiryOut, status_code=status.HTTP_201_CREATED)
# async def create_inquiry(
#     payload: InquiryCreate,
#     user: AuthenticatedUser = Depends(require_user),
#     db: AsyncSession = Depends(get_db),
# ):
#     try:
#         fac_uuid = uuid.UUID(payload.facility_id)
#     except ValueError:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")
 
#     facility = (
#         await db.execute(
#             select(
#                 Facility.id, Facility.name, Facility.facility_type_category,
#                 Facility.facility_type, Facility.state, Facility.city,
#             ).where(Facility.id == fac_uuid)
#         )
#     ).first()
#     if facility is None:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
 
#     profile = await ensure_profile_exists(db, user)
 
#     inquiry = Inquiry(
#         user_id=uuid.UUID(user.user_id),
#         # Snapshot the requester from their profile (fall back to token claims).
#         user_name=profile.full_name or user.full_name,
#         user_email=profile.email or user.email,
#         facility_id=fac_uuid,
#         # Snapshot the facility so the lead is self-explanatory. Prefer the
#         # standardized category; fall back to the raw type.
#         facility_name=facility.name,
#         facility_type_category=facility.facility_type_category or facility.facility_type,
#         state=facility.state,
#         city=facility.city,
#         message=payload.message,
#         budget=payload.budget,
#         contact_phone=payload.contact_phone,
#         contact_time_preference=payload.contact_time_preference,
#     )
#     db.add(inquiry)
#     await db.commit()
#     await db.refresh(inquiry)
#     return InquiryOut.model_validate(inquiry)
 
 
# @router.get("/me", response_model=list[InquiryOut])
# async def list_my_inquiries(
#     user: AuthenticatedUser = Depends(require_user),
#     db: AsyncSession = Depends(get_db),
# ):
#     stmt = (
#         select(Inquiry)
#         .where(Inquiry.user_id == uuid.UUID(user.user_id))
#         .order_by(Inquiry.created_at.desc())
#     )
#     rows = (await db.execute(stmt)).scalars().all()
#     return [InquiryOut.model_validate(r) for r in rows]
















 
import uuid
 
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
 
from app.core.database import get_db
from app.core.security import AuthenticatedUser
from app.dependencies import require_user
from app.models.facility import Facility
from app.models.inquiry import Inquiry
from app.schemas.inquiry import InquiryCreate, InquiryOut
from app.services.inquiry_email import send_inquiry_confirmation
from app.services.profile_service import ensure_profile_exists
 
router = APIRouter(prefix="/inquiries", tags=["inquiries"])
 
 
@router.post("", response_model=InquiryOut, status_code=status.HTTP_201_CREATED)
async def create_inquiry(
    payload: InquiryCreate,
    background: BackgroundTasks,
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        fac_uuid = uuid.UUID(payload.facility_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid facility id")
 
    facility = (
        await db.execute(
            select(
                Facility.id, Facility.name, Facility.facility_type_category,
                Facility.facility_type, Facility.state, Facility.city,
            ).where(Facility.id == fac_uuid)
        )
    ).first()
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
 
    profile = await ensure_profile_exists(db, user)
 
    inquiry = Inquiry(
        user_id=uuid.UUID(user.user_id),
        # Snapshot the requester from their profile (fall back to token claims).
        user_name=profile.full_name or user.full_name,
        user_email=profile.email or user.email,
        facility_id=fac_uuid,
        # Snapshot the facility so the lead is self-explanatory. Prefer the
        # standardized category; fall back to the raw type.
        facility_name=facility.name,
        facility_type_category=facility.facility_type_category or facility.facility_type,
        state=facility.state,
        city=facility.city,
        message=payload.message,
        budget=payload.budget,
        contact_phone=payload.contact_phone,
        contact_time_preference=payload.contact_time_preference,
    )
    db.add(inquiry)
    await db.commit()
    await db.refresh(inquiry)
 
    # Fire-and-forget confirmation email AFTER the response is returned, so the
    # user sees the success screen instantly and a mail hiccup never fails the
    # submit. send_inquiry_confirmation never raises.
    background.add_task(
        send_inquiry_confirmation,
        to_email=inquiry.user_email,
        name=inquiry.user_name,
        facility_name=inquiry.facility_name,
        location=", ".join(p for p in (inquiry.city, inquiry.state) if p) or None,
        budget=inquiry.budget,
        timeline=inquiry.contact_time_preference,
    )
 
    return InquiryOut.model_validate(inquiry)
 
 
@router.get("/me", response_model=list[InquiryOut])
async def list_my_inquiries(
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Inquiry)
        .where(Inquiry.user_id == uuid.UUID(user.user_id))
        .order_by(Inquiry.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [InquiryOut.model_validate(r) for r in rows]