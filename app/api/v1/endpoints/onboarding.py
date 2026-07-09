import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import AuthenticatedUser
from app.dependencies import require_user_or_guest
from app.models.profile import Profile
from app.schemas.profile import OnboardingPayload, ProfileOut

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("/complete", response_model=ProfileOut)
async def complete_onboarding(
    payload: OnboardingPayload,
    user: AuthenticatedUser = Depends(require_user_or_guest),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user.user_id)))
    profile = result.scalar_one_or_none()

    if profile is None:
        # Guests reach onboarding before /auth/sync-profile ever runs (no
        # Supabase account exists for them) -- create a minimal profile row
        # here so onboarding data has somewhere to live.
        profile = Profile(
            id=uuid.UUID(user.user_id),
            auth_provider=user.provider,
            is_guest=user.is_guest,
        )
        db.add(profile)

    profile.onboarding_data = payload.model_dump(exclude_none=True)
    profile.onboarding_completed = True

    await db.commit()
    await db.refresh(profile)
    return ProfileOut.model_validate(profile)


@router.get("/me", response_model=ProfileOut)
async def get_onboarding(
    user: AuthenticatedUser = Depends(require_user_or_guest),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user.user_id)))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return ProfileOut.model_validate(profile)
