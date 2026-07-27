import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import AuthenticatedUser
from app.dependencies import require_user
from app.models.profile import Profile
from app.schemas.profile import ProfileOut, ProfileUpdate

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me", response_model=ProfileOut)
async def get_my_profile(
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user.user_id)))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found -- call /auth/sync-profile first",
        )
    return ProfileOut.model_validate(profile)


@router.patch("/me", response_model=ProfileOut)
async def update_my_profile(
    payload: ProfileUpdate,
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user.user_id)))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    if payload.full_name is not None:
        profile.full_name = payload.full_name
    if payload.avatar_url is not None:
        profile.avatar_url = payload.avatar_url

    await db.commit()
    await db.refresh(profile)
    return ProfileOut.model_validate(profile)


@router.patch("/loved-one", response_model=ProfileOut)
async def update_loved_one(
    payload: dict,
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user.user_id)))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    onboarding_data = dict(profile.onboarding_data or {})
    onboarding_data["loved_one"] = payload
    profile.onboarding_data = onboarding_data

    await db.commit()
    await db.refresh(profile)
    return ProfileOut.model_validate(profile)
