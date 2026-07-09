"""Assessment (5-question quiz) results."""
import uuid as uuid_lib

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
    )
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False)
    recommended_care_type: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
