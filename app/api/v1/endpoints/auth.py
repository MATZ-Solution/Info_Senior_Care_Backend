# """
# Auth endpoints.

# Sign-up/sign-in itself (email, Google OAuth, Apple OAuth) happens entirely
# client-side via the Supabase SDK -- this backend never sees passwords or
# OAuth tokens directly. Once the client has a Supabase session, it calls
# `sync-profile` with that JWT so we can create/update our own `profiles` row
# (onboarding data, provider tracking, etc).
# """
# import uuid

# from fastapi import APIRouter, Depends, status
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.core.database import get_db
# from app.core.security import AuthenticatedUser
# from app.dependencies import require_user
# from app.models.profile import Profile
# from app.schemas.profile import GuestSessionOut, ProfileOut, SyncProfileResponse
# from app.services.guest_session import issue_guest_token

# router = APIRouter(prefix="/auth", tags=["auth"])


# @router.post("/sync-profile", response_model=SyncProfileResponse)
# async def sync_profile(
#     user: AuthenticatedUser = Depends(require_user),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Call this right after any Supabase sign-in (email, Google, or Apple).
#     Creates the profile row on first login; on subsequent logins, refreshes
#     provider/email/name/avatar in case they changed upstream (e.g. user
#     updated their Google profile photo).
#     """
#     result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user.user_id)))
#     profile = result.scalar_one_or_none()
#     created = False

#     if profile is None:
#         profile = Profile(
#             id=uuid.UUID(user.user_id),
#             email=user.email,
#             full_name=user.full_name,
#             avatar_url=user.avatar_url,
#             auth_provider=user.provider,
#             is_guest=False,
#         )
#         db.add(profile)
#         created = True
#     else:
#         profile.email = user.email or profile.email
#         profile.full_name = user.full_name or profile.full_name
#         profile.avatar_url = user.avatar_url or profile.avatar_url
#         # Note: we deliberately do NOT overwrite auth_provider on an existing
#         # profile if they now sign in via a different method (e.g. linked
#         # Google after originally signing up with email) -- the first
#         # provider used remains the recorded one unless product decides
#         # otherwise. Remove this comment/guard if that should change.

#     await db.commit()
#     await db.refresh(profile)

#     return SyncProfileResponse(profile=ProfileOut.model_validate(profile), created=created)


# @router.post("/guest", response_model=GuestSessionOut)
# async def create_guest_session():
#     """
#     Issues a lightweight guest token (no Supabase account created). The
#     client stores this and sends it as a normal Bearer token; endpoints
#     that accept guests use the `require_user_or_guest` dependency.
#     """
#     token = issue_guest_token()
#     return GuestSessionOut(access_token=token)







# """
# Auth endpoints.

# Sign-up/sign-in itself (email, Google OAuth, Apple OAuth) normally happens
# entirely client-side via the Supabase SDK -- this backend never needs to see
# passwords or OAuth tokens directly. Once the client has a Supabase session,
# it calls `sync-profile` with that JWT so we can create/update our own
# `profiles` row (onboarding data, provider tracking, etc).

# `/auth/signup` and `/auth/signin` below are a thin convenience proxy to
# Supabase's own email/password Auth REST API, added ONLY so this backend can
# be manually tested end-to-end (Swagger UI / Postman) without needing a
# separate curl call to Supabase or a mobile client. They are plain pass-
# throughs -- no password ever touches our own database, it goes straight to
# Supabase over HTTPS exactly like the client SDK would send it. The real
# mobile app should keep using the Supabase SDK directly for sign-up/sign-in;
# these two routes exist for local/dev testing convenience.
# """
# import uuid

# import httpx
# from fastapi import APIRouter, Depends, HTTPException, status
# from sqlalchemy import select
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.core.config import settings
# from app.core.database import get_db
# from app.core.security import AuthenticatedUser
# from app.dependencies import require_user
# from app.models.profile import Profile
# from app.schemas.profile import (
#     GuestSessionOut,
#     ProfileOut,
#     SigninRequest,
#     SignupRequest,
#     SupabaseAuthResponse,
#     SyncProfileResponse,
# )
# from app.services.guest_session import issue_guest_token

# router = APIRouter(prefix="/auth", tags=["auth"])


# @router.post("/signup", response_model=SupabaseAuthResponse)
# async def signup(payload: SignupRequest):
#     """
#     Dev/testing convenience only (see module docstring). Proxies straight to
#     Supabase's email/password signup endpoint.

#     NOTE: if your Supabase project has "Confirm email" turned on (the
#     default), the returned user will NOT have an access_token yet -- Supabase
#     only issues one after the confirmation link is clicked (or you can
#     manually confirm the user, or turn "Confirm email" off, in the Supabase
#     dashboard for local testing).
#     """
#     async with httpx.AsyncClient() as client:
#         resp = await client.post(
#             f"{settings.SUPABASE_URL}/auth/v1/signup",
#             headers={
#                 "apikey": settings.SUPABASE_ANON_KEY,
#                 "Content-Type": "application/json",
#             },
#             json={"email": payload.email, "password": payload.password},
#         )

#     data = resp.json()
#     if resp.status_code >= 400:
#         raise HTTPException(status_code=resp.status_code, detail=data)

#     return SupabaseAuthResponse(
#         access_token=data.get("access_token"),
#         token_type=data.get("token_type"),
#         expires_in=data.get("expires_in"),
#         refresh_token=data.get("refresh_token"),
#         user=data if data.get("id") else data.get("user"),
#         raw=data,
#     )


# @router.post("/signin", response_model=SupabaseAuthResponse)
# async def signin(payload: SigninRequest):
#     """
#     Dev/testing convenience only (see module docstring). Proxies straight to
#     Supabase's email/password sign-in (password grant) endpoint and hands
#     back the real access_token -- use this token as your Bearer token
#     against every other endpoint (starting with /auth/sync-profile).
#     """
#     async with httpx.AsyncClient() as client:
#         resp = await client.post(
#             f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=password",
#             headers={
#                 "apikey": settings.SUPABASE_ANON_KEY,
#                 "Content-Type": "application/json",
#             },
#             json={"email": payload.email, "password": payload.password},
#         )

#     data = resp.json()
#     if resp.status_code >= 400:
#         raise HTTPException(status_code=resp.status_code, detail=data)

#     return SupabaseAuthResponse(
#         access_token=data.get("access_token"),
#         token_type=data.get("token_type"),
#         expires_in=data.get("expires_in"),
#         refresh_token=data.get("refresh_token"),
#         user=data.get("user"),
#         raw=data,
#     )


# @router.post("/sync-profile", response_model=SyncProfileResponse)
# async def sync_profile(
#     user: AuthenticatedUser = Depends(require_user),
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Call this right after any Supabase sign-in (email, Google, or Apple).
#     Creates the profile row on first login; on subsequent logins, refreshes
#     provider/email/name/avatar in case they changed upstream (e.g. user
#     updated their Google profile photo).
#     """
#     result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user.user_id)))
#     profile = result.scalar_one_or_none()
#     created = False

#     if profile is None:
#         profile = Profile(
#             id=uuid.UUID(user.user_id),
#             email=user.email,
#             full_name=user.full_name,
#             avatar_url=user.avatar_url,
#             auth_provider=user.provider,
#             is_guest=False,
#         )
#         db.add(profile)
#         created = True
#     else:
#         profile.email = user.email or profile.email
#         profile.full_name = user.full_name or profile.full_name
#         profile.avatar_url = user.avatar_url or profile.avatar_url
#         # Note: we deliberately do NOT overwrite auth_provider on an existing
#         # profile if they now sign in via a different method (e.g. linked
#         # Google after originally signing up with email) -- the first
#         # provider used remains the recorded one unless product decides
#         # otherwise. Remove this comment/guard if that should change.

#     await db.commit()
#     await db.refresh(profile)

#     return SyncProfileResponse(profile=ProfileOut.model_validate(profile), created=created)


# @router.post("/guest", response_model=GuestSessionOut)
# async def create_guest_session():
#     """
#     Issues a lightweight guest token (no Supabase account created). The
#     client stores this and sends it as a normal Bearer token; endpoints
#     that accept guests use the `require_user_or_guest` dependency.
#     """
#     token = issue_guest_token()
#     return GuestSessionOut(access_token=token)











"""
Auth endpoints.

Sign-up/sign-in itself (email, Google OAuth, Apple OAuth) normally happens
entirely client-side via the Supabase SDK -- this backend never needs to see
passwords or OAuth tokens directly. Once the client has a Supabase session,
it calls `sync-profile` with that JWT so we can create/update our own
`profiles` row (onboarding data, provider tracking, etc).

`/auth/signup` and `/auth/signin` below are a thin convenience proxy to
Supabase's own email/password Auth REST API, added ONLY so this backend can
be manually tested end-to-end (Swagger UI / Postman) without needing a
separate curl call to Supabase or a mobile client. They are plain pass-
throughs -- no password ever touches our own database, it goes straight to
Supabase over HTTPS exactly like the client SDK would send it. The real
mobile app should keep using the Supabase SDK directly for sign-up/sign-in;
these two routes exist for local/dev testing convenience.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.http_client import ipv4_only_request
from app.core.security import AuthenticatedUser
from app.dependencies import require_user
from app.models.profile import Profile
from app.schemas.profile import (
    GuestSessionOut,
    ProfileOut,
    SigninRequest,
    SignupRequest,
    SupabaseAuthResponse,
    SyncProfileResponse,
)
from app.services.guest_session import issue_guest_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=SupabaseAuthResponse)
async def signup(payload: SignupRequest):
    """
    Dev/testing convenience only (see module docstring). Proxies straight to
    Supabase's email/password signup endpoint.

    NOTE: if your Supabase project has "Confirm email" turned on (the
    default), the returned user will NOT have an access_token yet -- Supabase
    only issues one after the confirmation link is clicked (or you can
    manually confirm the user, or turn "Confirm email" off, in the Supabase
    dashboard for local testing).
    """
    resp = await ipv4_only_request(
        "POST",
        f"{settings.SUPABASE_URL}/auth/v1/signup",
        headers={
            "apikey": settings.SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
        },
        json={"email": payload.email, "password": payload.password},
    )

    data = resp.json()
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=data)

    return SupabaseAuthResponse(
        access_token=data.get("access_token"),
        token_type=data.get("token_type"),
        expires_in=data.get("expires_in"),
        refresh_token=data.get("refresh_token"),
        user=data if data.get("id") else data.get("user"),
        raw=data,
    )


@router.post("/signin", response_model=SupabaseAuthResponse)
async def signin(payload: SigninRequest):
    """
    Dev/testing convenience only (see module docstring). Proxies straight to
    Supabase's email/password sign-in (password grant) endpoint and hands
    back the real access_token -- use this token as your Bearer token
    against every other endpoint (starting with /auth/sync-profile).
    """
    resp = await ipv4_only_request(
        "POST",
        f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={
            "apikey": settings.SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
        },
        json={"email": payload.email, "password": payload.password},
    )

    data = resp.json()
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=data)

    return SupabaseAuthResponse(
        access_token=data.get("access_token"),
        token_type=data.get("token_type"),
        expires_in=data.get("expires_in"),
        refresh_token=data.get("refresh_token"),
        user=data.get("user"),
        raw=data,
    )


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