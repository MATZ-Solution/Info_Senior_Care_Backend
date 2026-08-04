# """Assessment (5-question quiz) schemas."""
# from datetime import datetime
# from typing import Optional

# from app.schemas.common import UUIDStrMixin
# from pydantic import BaseModel, ConfigDict, Field


# class AssessmentSubmit(BaseModel):
#     answers: dict = Field(..., description="e.g. {'q1': 'answer_a', 'q2': 'answer_c', ...}")


# class AssessmentOut(UUIDStrMixin):
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     answers: dict
#     recommended_care_type: Optional[str] = None
#     created_at: datetime


# class AssessmentResult(BaseModel):
#     assessment: AssessmentOut
#     matched_facility_count: int












"""Assessment (5-question quiz) schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.recommendation_weights import ASSESSMENT_VERSION
from app.schemas.common import UUIDStrMixin


class AssessmentSubmit(BaseModel):
    """
    Request body. ``answers`` maps question ids to selected option ids, e.g.
    ``{"q1": "B", "q2": "C", "q3": "C", "q4": "A", "q5": "B"}``. Kept as a loose
    dict for forward/backward compatibility; option-level validation is handled
    tolerantly by the recommendation engine.
    """

    answers: dict = Field(
        ..., description='Question -> option map, e.g. {"q1": "B", "q2": "C", ...}'
    )
    assessment_version: str = Field(
        default=ASSESSMENT_VERSION,
        description="Questionnaire version the answers correspond to.",
    )


class CategoryScore(BaseModel):
    """One ranked care category and its normalised 0-100 score."""

    type: str
    score: int


class AssessmentOut(UUIDStrMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    answers: dict
    recommended_care_type: Optional[str] = None

    # New, additive fields. Older clients that only read recommended_care_type
    # keep working unchanged.
    recommended_types: list[CategoryScore] = Field(default_factory=list)
    confidence_score: Optional[int] = None
    explanation: list[str] = Field(default_factory=list)
    assessment_version: str = ASSESSMENT_VERSION

    created_at: datetime

    @classmethod
    def from_model(cls, assessment) -> "AssessmentOut":
        """
        Build the response from a persisted ``Assessment``. The ranked list,
        confidence, and explanation are read back from the stored ``result``
        JSON so both the submit and the "latest" endpoints return an identical
        shape.
        """
        result = assessment.result or {}
        return cls(
            id=assessment.id,
            answers=assessment.answers,
            recommended_care_type=assessment.recommended_care_type,
            recommended_types=result.get("recommended_types", []),
            confidence_score=result.get("confidence_score"),
            explanation=result.get("explanation", []),
            assessment_version=getattr(assessment, "assessment_version", ASSESSMENT_VERSION),
            created_at=assessment.created_at,
        )


class AssessmentResult(BaseModel):
    assessment: AssessmentOut
    matched_facility_count: int