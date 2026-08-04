"""
Recommendation engine for the care assessment.

Given a set of assessment answers, this engine decides which of the supported
care categories best match, how confident that match is, and why. It is a pure,
in-memory component:

  * It performs NO database or network I/O.
  * It has no FastAPI / SQLAlchemy / Pydantic dependencies.
  * It is deterministic and side-effect free, so it is trivially unit-testable.

All scoring data lives in ``app.core.recommendation_weights`` -- this module only
implements the algorithm. Complexity is O(questions x categories), which for the
fixed v1 questionnaire is effectively constant and runs in well under a
millisecond.

Algorithm
---------
1. For each answered question, look up the selected option in the scoring matrix
   and add ``question_weight * option_points`` to every category that option
   touches.
2. Normalise the raw scores to a 0-100 scale (top category -> 100).
3. Rank categories high-to-low (stable tie-break by declaration order).
4. Derive a confidence percentage from the separation between the top two
   categories -- a clear winner is high-confidence; a near-tie is low.
5. Build a human-readable explanation from the answers that drove the winner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.recommendation_weights import (
    ASSESSMENT_VERSION,
    EXPLANATION_TEMPLATES,
    QUESTION_WEIGHTS,
    SCORING_MATRIX,
    CareCategory,
    normalize_option_id,
    normalize_question_id,
)

logger = logging.getLogger("app.services.recommendation_engine")


@dataclass(frozen=True)
class CategoryScore:
    """A single ranked care category and its normalised 0-100 score."""

    type: str
    score: int


@dataclass(frozen=True)
class RecommendationResult:
    """
    Structured output of the engine.

    ``recommended_care_type`` is the top-ranked category (or ``None`` when the
    assessment carries no usable signal) and is kept for backward compatibility
    with the existing API. ``recommended_types`` is the full ranked list.
    """

    recommended_care_type: str | None
    recommended_types: list[CategoryScore] = field(default_factory=list)
    confidence_score: int = 0
    explanation: list[str] = field(default_factory=list)
    assessment_version: str = ASSESSMENT_VERSION

    def as_dict(self) -> dict:
        """Serialise to plain JSON-friendly primitives (for persistence/API)."""
        return {
            "recommended_care_type": self.recommended_care_type,
            "recommended_types": [
                {"type": cs.type, "score": cs.score} for cs in self.recommended_types
            ],
            "confidence_score": self.confidence_score,
            "explanation": list(self.explanation),
            "assessment_version": self.assessment_version,
        }


class RecommendationEngine:
    """
    Stateless recommender. Construct once and reuse; ``recommend`` is safe to
    call concurrently because it holds no per-call state on the instance.
    """

    def __init__(self, version: str = ASSESSMENT_VERSION) -> None:
        self._version = version

    # -- public API ---------------------------------------------------------

    def recommend(self, answers: dict | None) -> RecommendationResult:
        """
        Score an assessment and return a ranked recommendation.

        ``answers`` is a mapping of ``{question_id: option_id}`` (e.g.
        ``{"q1": "B", "q2": "C"}``). Unknown question ids, unknown option ids,
        and missing questions are tolerated -- they simply contribute nothing --
        so partial or malformed input degrades gracefully instead of raising.
        """
        cleaned = self._clean_answers(answers)
        raw_scores = self._compute_raw_scores(cleaned)
        ranked = self._rank(raw_scores)

        if not ranked or ranked[0].score <= 0:
            logger.info("Assessment produced no usable signal; returning empty recommendation")
            return RecommendationResult(
                recommended_care_type=None,
                recommended_types=ranked,
                confidence_score=0,
                explanation=["Not enough information to make a recommendation."],
                assessment_version=self._version,
            )

        top = ranked[0]
        confidence = self._confidence(ranked)
        explanation = self._build_explanation(cleaned, CareCategory(top.type))

        return RecommendationResult(
            recommended_care_type=top.type,
            recommended_types=ranked,
            confidence_score=confidence,
            explanation=explanation,
            assessment_version=self._version,
        )

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _clean_answers(answers: dict | None) -> dict[str, str]:
        """Normalise ids and drop non-string / empty entries. Never raises."""
        if not isinstance(answers, dict):
            return {}
        cleaned: dict[str, str] = {}
        for raw_q, raw_o in answers.items():
            if raw_q is None or raw_o is None:
                continue
            qid = normalize_question_id(raw_q)
            oid = normalize_option_id(raw_o)
            if qid and oid:
                cleaned[qid] = oid
        return cleaned

    @staticmethod
    def _compute_raw_scores(answers: dict[str, str]) -> dict[CareCategory, float]:
        """Weighted-sum every answer's contribution across all categories."""
        scores: dict[CareCategory, float] = {category: 0.0 for category in CareCategory}

        for qid, oid in answers.items():
            options = SCORING_MATRIX.get(qid)
            if options is None:
                logger.debug("Ignoring unknown question id %r", qid)
                continue
            contributions = options.get(oid)
            if contributions is None:
                logger.debug("Ignoring unknown option %r for question %r", oid, qid)
                continue
            weight = QUESTION_WEIGHTS.get(qid, 0.0)
            for category, points in contributions.items():
                scores[category] += weight * points

        return scores

    @staticmethod
    def _rank(raw_scores: dict[CareCategory, float]) -> list[CategoryScore]:
        """
        Normalise to 0-100 and sort high-to-low.

        Ties break by the category's declaration order in ``CareCategory`` so the
        output is fully deterministic. When every score is zero, all categories
        rank at 0 (still returned, so callers can inspect the full list).
        """
        category_order = {category: index for index, category in enumerate(CareCategory)}
        max_raw = max(raw_scores.values(), default=0.0)

        ranked: list[CategoryScore] = []
        for category, raw in raw_scores.items():
            normalized = round((raw / max_raw) * 100) if max_raw > 0 else 0
            ranked.append(CategoryScore(type=category.value, score=normalized))

        ranked.sort(
            key=lambda cs: (-cs.score, category_order[CareCategory(cs.type)])
        )
        return ranked

    @staticmethod
    def _confidence(ranked: list[CategoryScore]) -> int:
        """
        Confidence = normalised gap between the top two categories.

        A dominant winner (second place far behind) yields high confidence; a
        near-tie yields low confidence; an exact tie yields 0. Bounded to 0-100.
        """
        if not ranked or ranked[0].score <= 0:
            return 0
        top1 = ranked[0].score
        top2 = ranked[1].score if len(ranked) > 1 else 0
        confidence = round(((top1 - top2) / top1) * 100)
        return max(0, min(100, confidence))

    @staticmethod
    def _build_explanation(
        answers: dict[str, str], winner: CareCategory
    ) -> list[str]:
        """
        Collect the reason phrases for the answers that pushed the winning
        category, in question order. Falls back to a generic line if, for some
        edge case, no specific driver is found.
        """
        reasons: list[str] = []
        for qid in QUESTION_WEIGHTS:  # deterministic question order
            oid = answers.get(qid)
            if oid is None:
                continue
            contributions = SCORING_MATRIX.get(qid, {}).get(oid)
            if not contributions or contributions.get(winner, 0) <= 0:
                continue
            phrase = EXPLANATION_TEMPLATES.get(qid, {}).get(oid)
            if phrase:
                reasons.append(phrase)

        if not reasons:
            reasons.append(f"Best overall match for the provided answers: {winner.value}")
        return reasons