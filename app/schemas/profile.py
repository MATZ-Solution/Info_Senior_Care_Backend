# """Profile / onboarding schemas."""
# from typing import Optional

# from app.schemas.common import UUIDStrMixin
# from pydantic import BaseModel, ConfigDict


# class ProfileOut(UUIDStrMixin):
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     email: Optional[str] = None
#     full_name: Optional[str] = None
#     avatar_url: Optional[str] = None
#     auth_provider: str
#     is_guest: bool
#     onboarding_data: Optional[dict] = None
#     onboarding_completed: bool


# class ProfileUpdate(BaseModel):
#     full_name: Optional[str] = None
#     avatar_url: Optional[str] = None


# class OnboardingPayload(BaseModel):
#     """
#     Matches the 3-step onboarding flow (who you are, loved one, location+radius)
#     -- kept as one flexible JSON blob since the exact field set is still
#     evolving on the product side; each step's fields become keys here.
#     """
#     who_you_are: Optional[dict] = None
#     loved_one: Optional[dict] = None
#     location: Optional[dict] = None


# class SyncProfileResponse(BaseModel):
#     profile: ProfileOut
#     created: bool


# class GuestSessionOut(BaseModel):
#     access_token: str
#     token_type: str = "bearer"




















# """Profile / onboarding schemas."""
# from typing import Optional

# from app.schemas.common import UUIDStrMixin
# from pydantic import BaseModel, ConfigDict, Field


# class ProfileOut(UUIDStrMixin):
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     email: Optional[str] = None
#     full_name: Optional[str] = None
#     avatar_url: Optional[str] = None
#     auth_provider: str
#     is_guest: bool
#     onboarding_data: Optional[dict] = None
#     onboarding_completed: bool


# class SignupRequest(BaseModel):
#     email: str
#     password: str = Field(min_length=6)


# class SigninRequest(BaseModel):
#     email: str
#     password: str


# class SupabaseAuthResponse(BaseModel):
#     """
#     Thin pass-through of whatever Supabase's own Auth API returns, so the
#     client (or Postman/Swagger, during manual testing) gets the real
#     access_token straight from Supabase without our backend reshaping it.
#     """
#     access_token: Optional[str] = None
#     token_type: Optional[str] = None
#     expires_in: Optional[int] = None
#     refresh_token: Optional[str] = None
#     user: Optional[dict] = None
#     # Present instead of the above if Supabase returned an error, or if
#     # signup succeeded but email confirmation is still pending (in which
#     # case Supabase returns the created user with no access_token yet).
#     raw: Optional[dict] = None


# class ProfileUpdate(BaseModel):
#     full_name: Optional[str] = None
#     avatar_url: Optional[str] = None


# class OnboardingPayload(BaseModel):
#     """
#     Matches the 3-step onboarding flow (who you are, loved one, location+radius)
#     -- kept as one flexible JSON blob since the exact field set is still
#     evolving on the product side; each step's fields become keys here.
#     """
#     who_you_are: Optional[dict] = None
#     loved_one: Optional[dict] = None
#     location: Optional[dict] = None


# class SyncProfileResponse(BaseModel):
#     profile: ProfileOut
#     created: bool


# class GuestSessionOut(BaseModel):
#     access_token: str
#     token_type: str = "bearer"






















"""Profile / onboarding schemas."""
from typing import Optional

from app.schemas.common import UUIDStrMixin
from pydantic import BaseModel, ConfigDict, Field


class ProfileOut(UUIDStrMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    auth_provider: str
    is_guest: bool
    onboarding_data: Optional[dict] = None
    onboarding_completed: bool


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)


class SigninRequest(BaseModel):
    email: str
    password: str


class SupabaseAuthResponse(BaseModel):
    """
    Thin pass-through of whatever Supabase's own Auth API returns, so the
    client (or Postman/Swagger, during manual testing) gets the real
    access_token straight from Supabase without our backend reshaping it.
    """
    access_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    user: Optional[dict] = None
    # Present instead of the above if Supabase returned an error, or if
    # signup succeeded but email confirmation is still pending (in which
    # case Supabase returns the created user with no access_token yet).
    raw: Optional[dict] = None


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class OnboardingPayload(BaseModel):
    """
    Matches the onboarding flow (loved one, location+radius). No
    "who you are"/relationship field -- deliberately dropped; the
    account's own name (profile.full_name, from signup) already covers
    identity, and the product doesn't need a separate relationship field
    at this time.
    """
    loved_one: Optional[dict] = None
    location: Optional[dict] = None


class SyncProfileResponse(BaseModel):
    profile: ProfileOut
    created: bool


class GuestSessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"