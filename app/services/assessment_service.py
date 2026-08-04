"""
Assessment orchestration service.

Sits between the API endpoint and the (pure) recommendation engine. Its only
responsibilities are:

  * validate the incoming answers,
  * run the recommendation engine,
  * ensure the user's profile row exists and persist the assessment,
  * return the saved row plus the structured recommendation.

It deliberately contains NO scoring logic (that lives in
``RecommendationEngine``) and does NOT query the facilities table (that stays in
the search layer / endpoint) -- keeping each layer single-responsibility.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.recommendation_weights import ASSESSMENT_VERSION
from app.core.security import AuthenticatedUser
from app.models.assessment import Assessment
from app.services.profile_service import ensure_profile_exists
from app.services.recommendation_engine import RecommendationEngine, RecommendationResult

logger = logging.getLogger("app.services.assessment_service")


class AssessmentService:
    """
    Coordinates assessment submission. The recommendation engine is injected so
    it can be swapped or mocked in tests (Dependency Inversion).
    """

    def __init__(self, engine: RecommendationEngine | None = None) -> None:
        self._engine = engine or RecommendationEngine()

    @staticmethod
    def _validate_answers(answers: object) -> dict:
        """
        Basic structural validation. Content-level tolerance (unknown/missing
        options) is intentionally handled inside the engine, so an assessment
        with partial answers still yields a best-effort recommendation.
        """
        if not isinstance(answers, dict):
            raise ValueError("`answers` must be an object mapping question ids to option ids")
        return answers

    async def submit(
        self,
        db: AsyncSession,
        user: AuthenticatedUser,
        answers: dict,
        assessment_version: str | None = None,
    ) -> tuple[Assessment, RecommendationResult]:
        """
        Validate, score, persist, and return the assessment.

        Returns the persisted ``Assessment`` model and the ``RecommendationResult``.
        The caller (endpoint) is responsible for any facility-count enrichment,
        keeping this service free of facility-search concerns.
        """
        version = assessment_version or ASSESSMENT_VERSION
        validated = self._validate_answers(answers)

        result = self._engine.recommend(validated)
        logger.info(
            "Assessment scored | user=%s | version=%s | top=%s | confidence=%s",
            user.user_id,
            version,
            result.recommended_care_type,
            result.confidence_score,
        )

        await ensure_profile_exists(db, user)

        assessment = Assessment(
            user_id=uuid.UUID(user.user_id),
            answers=validated,
            recommended_care_type=result.recommended_care_type,
            assessment_version=version,
            result=result.as_dict(),
        )
        db.add(assessment)
        await db.commit()
        await db.refresh(assessment)

        return assessment, result