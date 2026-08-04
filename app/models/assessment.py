# """Assessment (5-question quiz) results."""
# import uuid as uuid_lib

# from sqlalchemy import DateTime, ForeignKey, String, func
# from sqlalchemy.dialects.postgresql import JSONB, UUID
# from sqlalchemy.orm import Mapped, mapped_column

# from app.core.database import Base


# class Assessment(Base):
#     __tablename__ = "assessments"

#     id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
#     )
#     user_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
#     )
#     answers: Mapped[dict] = mapped_column(JSONB, nullable=False)
#     recommended_care_type: Mapped[str | None] = mapped_column(String(200))
#     created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())




















"""Assessment (5-question quiz) results."""

import uuid as uuid_lib

from sqlalchemy import DateTime, ForeignKey, String, func, text
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

    # Top-ranked care category. Kept as a plain string for backward compatibility
    # with the original API; populated from the highest-ranked recommendation.
    recommended_care_type: Mapped[str | None] = mapped_column(String(200))

    # Version of the questionnaire/scoring that produced this row, so historical
    # assessments stay interpretable after future scoring changes. server_default
    # keeps existing rows valid without a data backfill.
    assessment_version: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'v1'")
    )

    # Full structured recommendation (ranked list, confidence, explanation) as
    # produced by the engine. Nullable for rows written before this column
    # existed; new rows always populate it.
    result: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )