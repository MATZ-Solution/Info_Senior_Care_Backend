"""
Auth endpoints.

Sign-up/sign-in itself (email, Google OAuth, Apple OAuth) happens entirely
client-side via the Supabase SDK -- this backend never sees passwords or
OAuth tokens directly. Once the client has a Supabase session, it calls
`sync-profile` with that JWT so we can create/update our own `profiles` row
(onboarding data, provider tracking, etc).
"""
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import AuthenticatedUser
from app.dependencies import require_user
from app.models.profile import Profile
from app.schemas.profile import GuestSessionOut, ProfileOut, SyncProfileResponse
from app.services.guest_session import issue_guest_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/sync-profile", response_model=SyncProfileResponse)
async def sync_profile(
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Call this right after any Supabase sign-in (email, Google, or Apple).
    Creates the profile row on first login; on subsequent logins, refreshes
    provider/email/name/avatar in case they changed upstream (e.g. user
    updated their Google profile photo).
    """
    result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user.user_id)))
    profile = result.scalar_one_or_none()
    created = False

    if profile is None:
        profile = Profile(
            id=uuid.UUID(user.user_id),
            email=user.email,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            auth_provider=user.provider,
            is_guest=False,
        )
        db.add(profile)
        created = True
    else:
        profile.email = user.email or profile.email
        profile.full_name = user.full_name or profile.full_name
        profile.avatar_url = user.avatar_url or profile.avatar_url
        # Note: we deliberately do NOT overwrite auth_provider on an existing
        # profile if they now sign in via a different method (e.g. linked
        # Google after originally signing up with email) -- the first
        # provider used remains the recorded one unless product decides
        # otherwise. Remove this comment/guard if that should change.

    await db.commit()
    await db.refresh(profile)

    return SyncProfileResponse(profile=ProfileOut.model_validate(profile), created=created)


@router.post("/guest", response_model=GuestSessionOut)
async def create_guest_session():
    """
    Issues a lightweight guest token (no Supabase account created). The
    client stores this and sends it as a normal Bearer token; endpoints
    that accept guests use the `require_user_or_guest` dependency.
    """
    token = issue_guest_token()
    return GuestSessionOut(access_token=token)
