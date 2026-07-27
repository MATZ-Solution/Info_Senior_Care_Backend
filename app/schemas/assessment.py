"""Assessment (5-question quiz) schemas."""
from datetime import datetime
from typing import Optional

from app.schemas.common import UUIDStrMixin
from pydantic import BaseModel, ConfigDict, Field


class AssessmentSubmit(BaseModel):
    answers: dict = Field(..., description="e.g. {'q1': 'answer_a', 'q2': 'answer_c', ...}")


class AssessmentOut(UUIDStrMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    answers: dict
    recommended_care_type: Optional[str] = None
    created_at: datetime


class AssessmentResult(BaseModel):
    assessment: AssessmentOut
    matched_facility_count: int
