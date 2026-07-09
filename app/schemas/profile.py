"""Profile / onboarding schemas."""
from typing import Optional

from app.schemas.common import UUIDStrMixin
from pydantic import BaseModel, ConfigDict


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


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


class OnboardingPayload(BaseModel):
    """
    Matches the 3-step onboarding flow (who you are, loved one, location+radius)
    -- kept as one flexible JSON blob since the exact field set is still
    evolving on the product side; each step's fields become keys here.
    """
    who_you_are: Optional[dict] = None
    loved_one: Optional[dict] = None
    location: Optional[dict] = None


class SyncProfileResponse(BaseModel):
    profile: ProfileOut
    created: bool


class GuestSessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
