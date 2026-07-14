# """
# FastAPI dependency-injection wrappers around app.core.security.

# Three flavors, because different endpoints have different auth needs:
#   - require_user       -> 401 if no valid token (profile, saved, inquiries...)
#   - optional_user      -> None if no token (public browsing endpoints that
#                            personalize when logged in, e.g. recommended)
#   - require_user_or_guest -> accepts a real Supabase JWT OR our own
#                            lightweight guest-session token
# """
# from typing import Optional

# from fastapi import Depends, HTTPException, status
# from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# from app.core.security import (
#     AuthenticatedUser,
#     decode_supabase_jwt,
#     parse_authenticated_user,
# )
# from app.services.guest_session import verify_guest_token

# bearer_scheme = HTTPBearer(auto_error=False)


# async def require_user(
#     credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
# ) -> AuthenticatedUser:
#     if credentials is None or not credentials.credentials:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Missing authentication token",
#             headers={"WWW-Authenticate": "Bearer"},
#         )
#     payload = decode_supabase_jwt(credentials.credentials)
#     return parse_authenticated_user(payload)


# async def optional_user(
#     credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
# ) -> Optional[AuthenticatedUser]:
#     if credentials is None or not credentials.credentials:
#         return None
#     try:
#         payload = decode_supabase_jwt(credentials.credentials)
#         return parse_authenticated_user(payload)
#     except HTTPException:
#         return None


# async def require_user_or_guest(
#     credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
# ) -> AuthenticatedUser:
#     if credentials is None or not credentials.credentials:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Missing authentication token",
#         )
#     token = credentials.credentials

#     # Guest tokens are prefixed so we never mistakenly treat a real
#     # Supabase JWT as a guest token or vice versa.
#     if token.startswith("guest_"):
#         guest_id = verify_guest_token(token)
#         if guest_id is None:
#             raise HTTPException(
#                 status_code=status.HTTP_401_UNAUTHORIZED,
#                 detail="Invalid or expired guest session",
#             )
#         return AuthenticatedUser(
#             user_id=guest_id,
#             email=None,
#             provider="guest",
#             full_name=None,
#             avatar_url=None,
#             is_guest=True,
#         )

#     payload = decode_supabase_jwt(token)
#     return parse_authenticated_user(payload)
































"""
FastAPI dependency-injection wrappers around app.core.security.

Three flavors, because different endpoints have different auth needs:
  - require_user       -> 401 if no valid token (profile, saved, inquiries...)
  - optional_user      -> None if no token (public browsing endpoints that
                           personalize when logged in, e.g. recommended)
  - require_user_or_guest -> accepts a real Supabase JWT OR our own
                           lightweight guest-session token
"""
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import (
    AuthenticatedUser,
    decode_supabase_jwt,
    parse_authenticated_user,
)
from app.services.guest_session import verify_guest_token

bearer_scheme = HTTPBearer(auto_error=False)


async def require_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_supabase_jwt(credentials.credentials)
    return parse_authenticated_user(payload)


async def optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[AuthenticatedUser]:
    if credentials is None or not credentials.credentials:
        return None
    try:
        payload = decode_supabase_jwt(credentials.credentials)
        return parse_authenticated_user(payload)
    except HTTPException:
        return None


async def require_user_or_guest(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )
    token = credentials.credentials

    # Guest tokens are prefixed so we never mistakenly treat a real
    # Supabase JWT as a guest token or vice versa.
    if token.startswith("guest_"):
        guest_id = verify_guest_token(token)
        if guest_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired guest session",
            )
        return AuthenticatedUser(
            user_id=guest_id,
            email=None,
            provider="guest",
            full_name=None,
            avatar_url=None,
            is_guest=True,
        )

    payload = decode_supabase_jwt(token)
    return parse_authenticated_user(payload)


async def optional_user_or_guest(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[AuthenticatedUser]:
    """
    Like require_user_or_guest, but NEVER raises -- returns None for a
    missing/invalid/expired token instead of 401ing. For endpoints that are
    fully public but should personalize when there IS a valid session
    (real or guest), e.g. home-screen recommendations based on onboarding
    data a guest may have already filled in.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await require_user_or_guest(credentials)
    except HTTPException:
        return None

