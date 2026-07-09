"""Inquiry schemas."""
from datetime import datetime
from typing import Optional

from app.schemas.common import UUIDStrMixin
from pydantic import BaseModel, ConfigDict, Field


class InquiryCreate(BaseModel):
    facility_id: str
    message: Optional[str] = Field(default=None, max_length=2000)
    contact_phone: Optional[str] = Field(default=None, max_length=30)
    contact_time_preference: Optional[str] = Field(default=None, max_length=100)


class InquiryOut(UUIDStrMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    facility_id: str
    message: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_time_preference: Optional[str] = None
    status: str
    created_at: datetime
