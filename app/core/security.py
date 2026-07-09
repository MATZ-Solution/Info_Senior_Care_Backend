"""
Supabase JWT verification.

Supabase Auth issues the SAME kind of JWT no matter which provider the user
signed in with (email/password, Google OAuth, Apple OAuth, etc). The provider
used is embedded inside the token claims, so our backend does not need any
separate Google/Apple-specific verification logic -- Supabase already
verified the OAuth handshake before minting this JWT. We only need to:

  1. Verify the JWT signature/expiry (using the project's JWT secret)
  2. Read out `sub` (the Supabase user id), `email`, and the provider info
  3. Track which provider was used (email / google / apple) in our own
     `profiles` table, since the product wants to distinguish Google users
     from Apple users (e.g. for support, analytics, or provider-specific UI).
"""
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger("app.security")

bearer_scheme = HTTPBearer(auto_error=False)

# Providers we explicitly recognize and store on the profile.
# "guest" is our own synthetic provider for anonymous/guest sessions,
# not something Supabase issues -- handled separately in auth endpoints.
SUPPORTED_PROVIDERS = {"email", "google", "apple", "guest"}


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str          # Supabase auth.users.id (uuid, as string)
    email: Optional[str]
    provider: str          # one of SUPPORTED_PROVIDERS
    full_name: Optional[str]
    avatar_url: Optional[str]
    is_guest: bool = False


def _extract_provider(payload: dict) -> str:
    """
    Supabase puts the provider in app_metadata.provider (e.g. 'google',
    'apple', 'email'). Fall back to the first entry in
    app_metadata.providers if 'provider' itself is missing, and finally
    to 'email' as the safest default.
    """
    app_metadata = payload.get("app_metadata") or {}
    provider = app_metadata.get("provider")
    if not provider:
        providers_list = app_metadata.get("providers") or []
        provider = providers_list[0] if providers_list else "email"
    provider = str(provider).lower()
    return provider if provider in SUPPORTED_PROVIDERS else "email"


def decode_supabase_jwt(token: str) -> dict:
    """
    Verifies signature + expiry against SUPABASE_JWT_SECRET.
    Raises HTTPException(401) on any failure -- never leaks details of why
    (avoids helping attackers fingerprint the failure reason).
    """
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except JWTError as exc:
        logger.info("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def parse_authenticated_user(payload: dict) -> AuthenticatedUser:
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    user_metadata = payload.get("user_metadata") or {}
    provider = _extract_provider(payload)

    # Google puts the display name/picture under slightly different keys
    # than Apple/email -- normalize here so the rest of the app doesn't
    # need to know which provider was used.
    full_name = (
        user_metadata.get("full_name")
        or user_metadata.get("name")
        or None
    )
    avatar_url = (
        user_metadata.get("avatar_url")
        or user_metadata.get("picture")
        or None
    )

    return AuthenticatedUser(
        user_id=user_id,
        email=payload.get("email"),
        provider=provider,
        full_name=full_name,
        avatar_url=avatar_url,
        is_guest=False,
    )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = None,
) -> AuthenticatedUser:
    """
    NOTE: the real FastAPI dependency wrapper lives in app/dependencies.py
    (it wires in HTTPBearer via Depends). This function is the pure/testable
    core so it can be unit tested without spinning up FastAPI's DI.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )
    payload = decode_supabase_jwt(credentials.credentials)
    return parse_authenticated_user(payload)
