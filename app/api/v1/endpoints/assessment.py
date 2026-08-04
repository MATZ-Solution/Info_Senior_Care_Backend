# import uuid

# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy import func, select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.core.database import get_db
# from app.core.security import AuthenticatedUser
# from app.dependencies import require_user_or_guest
# from app.models.assessment import Assessment
# from app.models.facility import Facility
# from app.schemas.assessment import AssessmentOut, AssessmentResult, AssessmentSubmit
# from app.services.profile_service import ensure_profile_exists

# router = APIRouter(prefix="/assessment", tags=["assessment"])

# # Minimal placeholder scoring -- swap for real product logic. Keeping this
# # isolated in one function makes it trivial to replace without touching the
# # endpoint/persistence code.
# _CARE_TYPE_BY_ANSER_PATTERN = {
#     "memory_care": "Nursing Home",
#     "independent": "Assisted Living",
#     "medical_support": "Home Health",
#     "end_of_life": "Hospice",
# }


# def _score_answers(answers: dict) -> str:
#     primary_need = answers.get("primary_need")
#     return _CARE_TYPE_BY_ANSER_PATTERN.get(primary_need, "Assisted Living")


# @router.post("/submit", response_model=AssessmentResult, status_code=status.HTTP_201_CREATED)
# async def submit_assessment(
#     payload: AssessmentSubmit,
#     user: AuthenticatedUser = Depends(require_user_or_guest),
#     db: AsyncSession = Depends(get_db),
# ):
#     recommended = _score_answers(payload.answers)

#     await ensure_profile_exists(db, user)

#     assessment = Assessment(
#         user_id=uuid.UUID(user.user_id),
#         answers=payload.answers,
#         recommended_care_type=recommended,
#     )
#     db.add(assessment)
#     await db.commit()
#     await db.refresh(assessment)

#     count_stmt = select(func.count()).select_from(Facility).where(
#         Facility.facility_type == recommended, Facility.is_active.is_(True)
#     )
#     matched_count = (await db.execute(count_stmt)).scalar_one()

#     return AssessmentResult(
#         assessment=AssessmentOut.model_validate(assessment),
#         matched_facility_count=matched_count,
#     )


# @router.get("/me/latest", response_model=AssessmentOut)
# async def get_latest_assessment(
#     user: AuthenticatedUser = Depends(require_user_or_guest),
#     db: AsyncSession = Depends(get_db),
# ):
#     stmt = (
#         select(Assessment)
#         .where(Assessment.user_id == uuid.UUID(user.user_id))
#         .order_by(Assessment.created_at.desc())
#         .limit(1)
#     )
#     assessment = (await db.execute(stmt)).scalar_one_or_none()
#     if assessment is None:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment found")
#     return AssessmentOut.model_validate(assessment)














































# import uuid

# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy import func, select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.core.database import get_db
# from app.core.security import AuthenticatedUser
# from app.dependencies import require_user_or_guest
# from app.models.assessment import Assessment
# from app.models.facility import Facility
# from app.schemas.assessment import AssessmentOut, AssessmentResult, AssessmentSubmit
# from app.services.profile_service import ensure_profile_exists

# router = APIRouter(prefix="/assessment", tags=["assessment"])

# # Minimal placeholder scoring -- swap for real product logic. Keeping this
# # isolated in one function makes it trivial to replace without touching the
# # endpoint/persistence code.
# #
# # Values map to `Facility.facility_type_category` (the standardized field,
# # see scripts/import_facilities.py) -- NOT the raw `facility_type` column,
# # which has too many inconsistent real-world variants to match reliably.
# _CARE_TYPE_BY_ANSER_PATTERN = {
#     "memory_care": "Nursing Home / Skilled Nursing Facility",
#     "independent": "Residential Care / Assisted Living",
#     "medical_support": "Home Health Agency",
#     "end_of_life": "Hospice",
# }


# def _score_answers(answers: dict) -> str:
#     primary_need = answers.get("primary_need")
#     return _CARE_TYPE_BY_ANSER_PATTERN.get(primary_need, "Assisted Living")


# @router.post("/submit", response_model=AssessmentResult, status_code=status.HTTP_201_CREATED)
# async def submit_assessment(
#     payload: AssessmentSubmit,
#     user: AuthenticatedUser = Depends(require_user_or_guest),
#     db: AsyncSession = Depends(get_db),
# ):
#     recommended = _score_answers(payload.answers)

#     await ensure_profile_exists(db, user)

#     assessment = Assessment(
#         user_id=uuid.UUID(user.user_id),
#         answers=payload.answers,
#         recommended_care_type=recommended,
#     )
#     db.add(assessment)
#     await db.commit()
#     await db.refresh(assessment)

#     count_stmt = select(func.count()).select_from(Facility).where(
#         Facility.facility_type_category == recommended, Facility.is_active.is_(True)
#     )
#     matched_count = (await db.execute(count_stmt)).scalar_one()

#     return AssessmentResult(
#         assessment=AssessmentOut.model_validate(assessment),
#         matched_facility_count=matched_count,
#     )


# @router.get("/me/latest", response_model=AssessmentOut)
# async def get_latest_assessment(
#     user: AuthenticatedUser = Depends(require_user_or_guest),
#     db: AsyncSession = Depends(get_db),
# ):
#     stmt = (
#         select(Assessment)
#         .where(Assessment.user_id == uuid.UUID(user.user_id))
#         .order_by(Assessment.created_at.desc())
#         .limit(1)
#     )
#     assessment = (await db.execute(stmt)).scalar_one_or_none()
#     if assessment is None:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment found")
#     return AssessmentOut.model_validate(assessment)



























"""Assessment submission and retrieval endpoints.

Thin controllers: request/response handling and the facility-count lookup only.
All recommendation logic lives in ``RecommendationEngine`` and is orchestrated by
``AssessmentService``; the endpoint never scores anything itself.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import AuthenticatedUser
from app.dependencies import require_user_or_guest
from app.models.assessment import Assessment
from app.models.facility import Facility
from app.schemas.assessment import AssessmentOut, AssessmentResult, AssessmentSubmit
from app.services.assessment_service import AssessmentService

router = APIRouter(prefix="/assessment", tags=["assessment"])

# Stateless; safe to share a single instance across requests.
_assessment_service = AssessmentService()


async def _count_active_facilities(db: AsyncSession, care_type: str | None) -> int:
    """
    How many active facilities match the recommended category. Lives in the
    endpoint (search layer) rather than the engine/service so recommendation
    stays database-independent. Returns 0 when there is no recommendation.
    """
    if not care_type:
        return 0
    stmt = select(func.count()).select_from(Facility).where(
        Facility.facility_type_category == care_type,
        Facility.is_active.is_(True),
    )
    return (await db.execute(stmt)).scalar_one()


@router.post("/submit", response_model=AssessmentResult, status_code=status.HTTP_201_CREATED)
async def submit_assessment(
    payload: AssessmentSubmit,
    user: AuthenticatedUser = Depends(require_user_or_guest),
    db: AsyncSession = Depends(get_db),
):
    """Score, persist, and return the assessment plus a matching-facility count."""
    try:
        assessment, result = await _assessment_service.submit(
            db=db,
            user=user,
            answers=payload.answers,
            assessment_version=payload.assessment_version,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    matched_count = await _count_active_facilities(db, result.recommended_care_type)

    return AssessmentResult(
        assessment=AssessmentOut.from_model(assessment),
        matched_facility_count=matched_count,
    )


@router.get("/me/latest", response_model=AssessmentOut)
async def get_latest_assessment(
    user: AuthenticatedUser = Depends(require_user_or_guest),
    db: AsyncSession = Depends(get_db),
):
    """Return the caller's most recent assessment, or 404 if none exists."""
    stmt = (
        select(Assessment)
        .where(Assessment.user_id == uuid.UUID(user.user_id))
        .order_by(Assessment.created_at.desc())
        .limit(1)
    )
    assessment = (await db.execute(stmt)).scalar_one_or_none()
    if assessment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment found")
    return AssessmentOut.from_model(assessment)