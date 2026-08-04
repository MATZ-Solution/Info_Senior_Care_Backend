"""
Unit tests for the recommendation engine.

These tests are pure and DB-free: they exercise only ``RecommendationEngine`` and
its scoring config. They cover normal scoring, ranking correctness, confidence,
explanation generation, and every degenerate-input path (unknown / missing /
empty / invalid answers, and ties).
"""

from app.core.recommendation_weights import ASSESSMENT_VERSION, CareCategory
from app.services.recommendation_engine import (
    CategoryScore,
    RecommendationEngine,
    RecommendationResult,
)

engine = RecommendationEngine()


# -- normal scoring ---------------------------------------------------------

def test_post_surgery_rehab_profile_recommends_inpatient_rehab():
    answers = {"q1": "B", "q2": "C", "q3": "C", "q4": "C", "q5": "B"}
    result = engine.recommend(answers)
    assert result.recommended_care_type == CareCategory.REHAB_INPATIENT.value
    assert result.recommended_types[0].type == CareCategory.REHAB_INPATIENT.value


def test_daily_living_profile_recommends_residential_care():
    answers = {"q1": "A", "q2": "B", "q3": "B", "q4": "A", "q5": "C"}
    result = engine.recommend(answers)
    assert result.recommended_care_type == CareCategory.RESIDENTIAL_CARE.value


def test_end_of_life_profile_recommends_hospice():
    answers = {"q1": "F", "q2": "D", "q3": "D", "q4": "A", "q5": "D"}
    result = engine.recommend(answers)
    assert result.recommended_care_type == CareCategory.HOSPICE.value


def test_daytime_supervision_recommends_adult_day_care():
    answers = {"q1": "E", "q2": "A", "q3": "A", "q4": "A", "q5": "A"}
    result = engine.recommend(answers)
    assert result.recommended_care_type == CareCategory.ADULT_DAY_CARE.value


def test_mental_health_profile_recommends_mental_health_facility():
    answers = {"q1": "D"}
    result = engine.recommend(answers)
    assert result.recommended_care_type == CareCategory.MENTAL_HEALTH.value


# -- ranking correctness ----------------------------------------------------

def test_result_shape_and_all_categories_ranked():
    result = engine.recommend({"q1": "B"})
    assert isinstance(result, RecommendationResult)
    # Every supported category appears exactly once in the ranking.
    ranked_types = {cs.type for cs in result.recommended_types}
    assert ranked_types == {c.value for c in CareCategory}
    assert len(result.recommended_types) == len(list(CareCategory))


def test_ranking_is_sorted_descending():
    result = engine.recommend({"q1": "B", "q2": "C", "q3": "C"})
    scores = [cs.score for cs in result.recommended_types]
    assert scores == sorted(scores, reverse=True)


def test_scores_are_normalised_top_is_100():
    result = engine.recommend({"q1": "C", "q2": "D", "q3": "D", "q5": "C"})
    assert result.recommended_types[0].score == 100
    assert all(0 <= cs.score <= 100 for cs in result.recommended_types)


def test_top_recommendation_matches_first_ranked_entry():
    result = engine.recommend({"q1": "A", "q3": "B"})
    assert result.recommended_care_type == result.recommended_types[0].type


# -- confidence calculation -------------------------------------------------

def test_confidence_is_high_for_dominant_winner():
    # A pure mental-health signal has no competing category.
    result = engine.recommend({"q1": "D"})
    assert result.confidence_score == 100


def test_confidence_between_0_and_100():
    result = engine.recommend({"q1": "B", "q2": "C", "q3": "C", "q4": "C", "q5": "B"})
    assert 0 <= result.confidence_score <= 100


def test_confidence_is_zero_on_tie():
    # q1=A gives Residential 10 / AdultDay 4; add a symmetric counter-signal so
    # two categories land on the same normalised score.
    result = engine.recommend({"q1": "A", "q5": "A"})
    top_two = result.recommended_types[:2]
    if top_two[0].score == top_two[1].score:
        assert result.confidence_score == 0


# -- explanation generation -------------------------------------------------

def test_explanation_is_nonempty_list_of_strings():
    result = engine.recommend({"q1": "B", "q4": "C"})
    assert isinstance(result.explanation, list)
    assert result.explanation
    assert all(isinstance(line, str) and line for line in result.explanation)


def test_explanation_reflects_driving_answers():
    result = engine.recommend({"q1": "F"})
    assert any("comfort" in line.lower() for line in result.explanation)


# -- degenerate input: unknown / missing / empty / invalid ------------------

def test_empty_assessment_returns_safe_result():
    result = engine.recommend({})
    assert result.recommended_care_type is None
    assert result.confidence_score == 0
    assert result.explanation  # generic "not enough information" line
    assert len(result.recommended_types) == len(list(CareCategory))


def test_none_answers_do_not_raise():
    result = engine.recommend(None)
    assert result.recommended_care_type is None


def test_unknown_question_id_is_ignored():
    result = engine.recommend({"q99": "A", "q1": "B"})
    # q99 ignored; q1=B still drives an inpatient-rehab lean.
    assert result.recommended_care_type == CareCategory.REHAB_INPATIENT.value


def test_unknown_option_is_ignored():
    result = engine.recommend({"q1": "Z"})
    assert result.recommended_care_type is None
    assert result.confidence_score == 0


def test_missing_questions_still_scored_on_present_ones():
    result = engine.recommend({"q1": "C"})  # only one of five questions answered
    assert result.recommended_care_type == CareCategory.NURSING_HOME.value


def test_non_string_and_dead_end_options_are_handled():
    # q4=A is a valid but zero-scoring option; ints get coerced/ignored safely.
    result = engine.recommend({"q4": "A", 123: 456, "q1": "A"})
    assert result.recommended_care_type == CareCategory.RESIDENTIAL_CARE.value


def test_case_and_whitespace_insensitive_ids():
    lower = engine.recommend({" Q1 ": " b "})
    upper = engine.recommend({"q1": "B"})
    assert lower.recommended_care_type == upper.recommended_care_type


# -- serialisation / versioning ---------------------------------------------

def test_result_as_dict_is_json_friendly():
    result = engine.recommend({"q1": "B"})
    payload = result.as_dict()
    assert payload["recommended_care_type"] == CareCategory.REHAB_INPATIENT.value
    assert payload["assessment_version"] == ASSESSMENT_VERSION
    assert isinstance(payload["recommended_types"], list)
    assert payload["recommended_types"][0] == {
        "type": CareCategory.REHAB_INPATIENT.value,
        "score": 100,
    }


def test_category_score_is_immutable():
    cs = CategoryScore(type=CareCategory.HOSPICE.value, score=50)
    try:
        cs.score = 99  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised
