"""Saved facilities -- many-to-many between profiles and facilities."""
import uuid as uuid_lib

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SavedFacility(Base):
    __tablename__ = "saved_facilities"

    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    facility_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
