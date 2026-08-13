# """Inquiries -- a user requesting contact from a facility."""
# import uuid as uuid_lib

# from sqlalchemy import DateTime, ForeignKey, String, Text, func
# from sqlalchemy.dialects.postgresql import UUID
# from sqlalchemy.orm import Mapped, mapped_column

# from app.core.database import Base


# class Inquiry(Base):
#     __tablename__ = "inquiries"

#     id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
#     )
#     user_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
#     )
#     facility_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), nullable=False
#     )
#     message: Mapped[str | None] = mapped_column(Text)
#     contact_phone: Mapped[str | None] = mapped_column(String(30))
#     contact_time_preference: Mapped[str | None] = mapped_column(String(100))
#     status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
#     created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())





























# """Inquiries -- a user requesting contact from a facility."""
# import uuid as uuid_lib

# from sqlalchemy import DateTime, ForeignKey, String, Text, func
# from sqlalchemy.dialects.postgresql import UUID
# from sqlalchemy.orm import Mapped, mapped_column

# from app.core.database import Base


# class Inquiry(Base):
#     __tablename__ = "inquiries"

#     id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
#     )
#     user_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
#     )
#     # Snapshot of the requester (copied from their profile at submit time) so a
#     # lead is self-contained without joining back to profiles.
#     user_name: Mapped[str | None] = mapped_column(String(300))
#     user_email: Mapped[str | None] = mapped_column(String(300))
#     facility_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), nullable=False
#     )
#     # Human-readable snapshot of the facility at submit time, so a lead is
#     # self-explanatory without joining back to facilities.
#     facility_name: Mapped[str | None] = mapped_column(String(500))
#     facility_type_category: Mapped[str | None] = mapped_column(String(200))
#     message: Mapped[str | None] = mapped_column(Text)
#     contact_phone: Mapped[str | None] = mapped_column(String(30))
#     contact_time_preference: Mapped[str | None] = mapped_column(String(100))
#     status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
#     created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())




























# """Inquiries -- a user requesting contact from a facility."""
# import uuid as uuid_lib

# from sqlalchemy import DateTime, ForeignKey, String, Text, func
# from sqlalchemy.dialects.postgresql import UUID
# from sqlalchemy.orm import Mapped, mapped_column

# from app.core.database import Base


# class Inquiry(Base):
#     __tablename__ = "inquiries"

#     id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
#     )
#     user_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
#     )
#     # Snapshot of the requester (copied from their profile at submit time) so a
#     # lead is self-contained without joining back to profiles.
#     user_name: Mapped[str | None] = mapped_column(String(300))
#     user_email: Mapped[str | None] = mapped_column(String(300))
#     facility_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), nullable=False
#     )
#     # Human-readable snapshot of the facility at submit time, so a lead is
#     # self-explanatory without joining back to facilities.
#     facility_name: Mapped[str | None] = mapped_column(String(500))
#     facility_type_category: Mapped[str | None] = mapped_column(String(200))
#     # Where the facility is (snapshot from the facility). The admin view
#     # combines these into a single "City, State" location string.
#     state: Mapped[str | None] = mapped_column(String(50))
#     city: Mapped[str | None] = mapped_column(String(200))
#     message: Mapped[str | None] = mapped_column(Text)
#     contact_phone: Mapped[str | None] = mapped_column(String(30))
#     contact_time_preference: Mapped[str | None] = mapped_column(String(100))
#     status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
#     created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())























"""Inquiries -- a user requesting contact from a facility."""
import uuid as uuid_lib

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Inquiry(Base):
    __tablename__ = "inquiries"

    id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
    )
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    # Snapshot of the requester (copied from their profile at submit time) so a
    # lead is self-contained without joining back to profiles.
    user_name: Mapped[str | None] = mapped_column(String(300))
    user_email: Mapped[str | None] = mapped_column(String(300))
    facility_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), nullable=False
    )
    # Human-readable snapshot of the facility at submit time, so a lead is
    # self-explanatory without joining back to facilities.
    facility_name: Mapped[str | None] = mapped_column(String(500))
    facility_type_category: Mapped[str | None] = mapped_column(String(200))
    # Where the facility is (snapshot from the facility). The admin view
    # combines these into a single "City, State" location string.
    state: Mapped[str | None] = mapped_column(String(50))
    city: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(Text)
    # Dedicated budget field, separate from the free-text message.
    budget: Mapped[str | None] = mapped_column(String(100))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    contact_time_preference: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())