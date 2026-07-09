"""
Shared helper: ensures a `profiles` row exists for the current user before
any write that has a foreign key to it (inquiries, assessments, saved
facilities). Real Supabase users normally get their profile row via
/auth/sync-profile right after login, but two cases can reach a write
endpoint without one:

  1. Guests -- there's no Supabase account to sync, so nothing calls
     sync-profile for them. Without this, their first inquiry/assessment
     would 500 with a foreign-key violation.
  2. A client that (due to a bug, a race, or an old app version) writes
     data before calling sync-profile.

Centralizing this avoids duplicating the same "get-or-create" logic in
every endpoint that writes user-owned data.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser
from app.models.profile import Profile


async def ensure_profile_exists(db: AsyncSession, user: AuthenticatedUser) -> Profile:
    user_uuid = uuid.UUID(user.user_id)
    result = await db.execute(select(Profile).where(Profile.id == user_uuid))
    profile = result.scalar_one_or_none()
    if profile is not None:
        return profile

    profile = Profile(
        id=user_uuid,
        email=user.email,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        auth_provider=user.provider,
        is_guest=user.is_guest,
    )
    db.add(profile)
    await db.flush()  # populate defaults without committing the caller's transaction early
    return profile
