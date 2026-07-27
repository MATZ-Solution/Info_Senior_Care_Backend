"""
Profile model.

IMPORTANT: We do NOT create our own `users` table with passwords -- Supabase
Auth already owns identity (auth.users, managed by Supabase, includes email,
Google, and Apple sign-in). This `profiles` table is OUR app-specific
extension of that identity: onboarding data, loved-one info, and which
provider they used (so support/analytics can distinguish Google vs Apple
vs email vs guest users, per product requirement).

`id` here is NOT auto-generated -- it must equal the Supabase auth.users.id
(the `sub` claim of their JWT), enforced by the API layer, not a DB FK,
because auth.users lives in Supabase's own `auth` schema which our
migrations don't manage.
"""
import uuid as uuid_lib

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid_lib.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    email: Mapped[str | None] = mapped_column(String(300))
    full_name: Mapped[str | None] = mapped_column(String(300))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))

    # 'email' | 'google' | 'apple' | 'guest' -- see app/core/security.py
    auth_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="email")

    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Onboarding: who-you-are, loved-one info, location+radius -- kept as
    # JSONB since these are small, read-mostly-as-a-whole blobs the client
    # renders directly; normalizing into columns would add migration
    # overhead for a fast-moving onboarding flow with no query need on
    # individual sub-fields yet.
    onboarding_data: Mapped[dict | None] = mapped_column(JSONB)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
