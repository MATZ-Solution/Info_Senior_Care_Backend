"""
Config-driven scoring data for the assessment recommendation engine.

This module holds *only data* -- no business logic. Every knob that decides
how an assessment maps to a care category lives here, so tuning recommendation
behaviour never requires touching engine/service code (Open/Closed principle).

Three things are defined:
  * ``CareCategory``      -- the 8 supported facility categories, as an Enum whose
                            values are the EXACT ``facilities.facility_type_category``
                            strings stored in the database. The recommendation
                            output must match these verbatim or the downstream
                            facility-count query returns nothing.
  * ``QUESTION_WEIGHTS``  -- relative importance of each assessment question.
  * ``SCORING_MATRIX``    -- for every (question, option), the raw points each
                            care category earns. A single option may (and usually
                            does) contribute to several categories.
  * ``EXPLANATION_TEMPLATES`` -- a human-readable reason per (question, option),
                            used to build the "Recommended because ..." list.

Versioning: ``ASSESSMENT_VERSION`` tags the *shape* of the questions/scoring so
historical assessments remain interpretable after future changes. Bump it
whenever the questions or the matrix change in a backward-incompatible way.
"""

from __future__ import annotations

from enum import Enum


ASSESSMENT_VERSION = "v1"


class CareCategory(str, Enum):
    """
    Supported facility categories.

    NOTE: the string values MUST stay byte-for-byte identical to the values in
    ``facilities.facility_type_category``. They are used directly in the
    facility-count query, so a mismatch silently yields a count of 0.
    """

    ADULT_DAY_CARE = "Adult Day Care"
    INTERMEDIATE_CARE = "Intermediate Care Facility (ICF/IID)"
    NURSING_HOME = "Nursing Home / Skilled Nursing Facility"
    MENTAL_HEALTH = "Mental/Behavioral Health Facility"
    REHAB_INPATIENT = "Rehabilitation - Inpatient"
    REHAB_OUTPATIENT = "Rehabilitation - Outpatient"
    RESIDENTIAL_CARE = "Residential Care / Assisted Living"
    HOSPICE = "Hospice"


# Relative importance of each question. Q1 (primary reason for seeking care) is
# the strongest single signal, hence the heavier weight. The values do not need
# to sum to 1.0 -- scores are normalised later -- but keeping them proportional
# to the product spec (40/20/20/10/10) makes the config readable.
QUESTION_WEIGHTS: dict[str, float] = {
    "q1": 0.40,
    "q2": 0.20,
    "q3": 0.20,
    "q4": 0.10,
    "q5": 0.10,
}


# (question_id) -> (option_id) -> {CareCategory: raw_points}
#
# Option ids are the single letters shown to the user (A, B, C, ...). Raw points
# are on a 0-10 scale per option; the engine multiplies them by the question
# weight above. An option that is a dead-end for scoring (e.g. "No rehab needed")
# maps to an empty dict rather than being omitted, so the option is still
# recognised as valid input.
SCORING_MATRIX: dict[str, dict[str, dict[CareCategory, int]]] = {
    # Q1 (40%) -- Primary reason for seeking care.
    "q1": {
        "A": {  # Help with daily living activities.
            CareCategory.RESIDENTIAL_CARE: 10,
            CareCategory.ADULT_DAY_CARE: 4,
        },
        "B": {  # Rehabilitation after surgery, injury, or illness.
            CareCategory.REHAB_INPATIENT: 10,
            CareCategory.REHAB_OUTPATIENT: 7,
            CareCategory.NURSING_HOME: 2,
        },
        "C": {  # Ongoing skilled medical or nursing care.
            CareCategory.NURSING_HOME: 10,
            CareCategory.INTERMEDIATE_CARE: 4,
            CareCategory.REHAB_INPATIENT: 3,
        },
        "D": {  # Serious mental or behavioural health concerns.
            CareCategory.MENTAL_HEALTH: 10,
        },
        "E": {  # Daytime supervision while living at home.
            CareCategory.ADULT_DAY_CARE: 10,
            CareCategory.RESIDENTIAL_CARE: 2,
        },
        "F": {  # Comfort-focused care for a serious/terminal illness.
            CareCategory.HOSPICE: 10,
        },
    },
    # Q2 (20%) -- Level of medical care needed.
    "q2": {
        "A": {  # No regular medical care.
            CareCategory.RESIDENTIAL_CARE: 6,
            CareCategory.ADULT_DAY_CARE: 6,
        },
        "B": {  # Occasional check-ups.
            CareCategory.RESIDENTIAL_CARE: 8,
            CareCategory.ADULT_DAY_CARE: 4,
            CareCategory.REHAB_OUTPATIENT: 2,
        },
        "C": {  # Daily skilled nursing supervision.
            CareCategory.NURSING_HOME: 9,
            CareCategory.INTERMEDIATE_CARE: 6,
            CareCategory.REHAB_INPATIENT: 3,
        },
        "D": {  # Continuous 24/7 medical care.
            CareCategory.NURSING_HOME: 10,
            CareCategory.INTERMEDIATE_CARE: 7,
            CareCategory.HOSPICE: 4,
        },
    },
    # Q3 (20%) -- Mobility and independence.
    "q3": {
        "A": {  # Completely independent.
            CareCategory.ADULT_DAY_CARE: 6,
            CareCategory.RESIDENTIAL_CARE: 3,
        },
        "B": {  # Needs some assistance.
            CareCategory.RESIDENTIAL_CARE: 9,
            CareCategory.ADULT_DAY_CARE: 4,
        },
        "C": {  # Cannot safely live alone.
            CareCategory.NURSING_HOME: 8,
            CareCategory.RESIDENTIAL_CARE: 6,
            CareCategory.INTERMEDIATE_CARE: 4,
        },
        "D": {  # Mostly bed-bound or wheelchair dependent.
            CareCategory.NURSING_HOME: 10,
            CareCategory.INTERMEDIATE_CARE: 8,
            CareCategory.HOSPICE: 2,
        },
    },
    # Q4 (10%) -- Rehabilitation / therapy needs.
    "q4": {
        "A": {},  # No rehabilitation needed -- valid, but scores nothing.
        "B": {  # Outpatient therapy.
            CareCategory.REHAB_OUTPATIENT: 10,
            CareCategory.RESIDENTIAL_CARE: 2,
        },
        "C": {  # Intensive inpatient rehabilitation.
            CareCategory.REHAB_INPATIENT: 10,
            CareCategory.NURSING_HOME: 3,
        },
        "D": {  # Unsure -- a light, hedged spread.
            CareCategory.REHAB_OUTPATIENT: 3,
            CareCategory.REHAB_INPATIENT: 2,
        },
    },
    # Q5 (10%) -- Expected duration / nature of care.
    "q5": {
        "A": {  # Daytime only.
            CareCategory.ADULT_DAY_CARE: 10,
        },
        "B": {  # Short-term recovery.
            CareCategory.REHAB_INPATIENT: 6,
            CareCategory.REHAB_OUTPATIENT: 6,
            CareCategory.NURSING_HOME: 2,
        },
        "C": {  # Long-term ongoing care.
            CareCategory.NURSING_HOME: 8,
            CareCategory.RESIDENTIAL_CARE: 7,
            CareCategory.INTERMEDIATE_CARE: 6,
        },
        "D": {  # End-of-life comfort care.
            CareCategory.HOSPICE: 10,
        },
    },
}


# (question_id) -> (option_id) -> reason phrase.
# The engine surfaces the reasons whose option contributed to the winning
# category, producing a targeted "Recommended because ..." list.
EXPLANATION_TEMPLATES: dict[str, dict[str, str]] = {
    "q1": {
        "A": "Needs help with daily living activities",
        "B": "Recovering after surgery, injury, or illness",
        "C": "Requires ongoing skilled medical or nursing care",
        "D": "Has serious mental or behavioural health needs",
        "E": "Needs daytime supervision while living at home",
        "F": "Needs comfort-focused care for a serious illness",
    },
    "q2": {
        "A": "Little to no regular medical care required",
        "B": "Needs occasional medical check-ups",
        "C": "Requires daily skilled nursing supervision",
        "D": "Requires continuous 24/7 medical care",
    },
    "q3": {
        "A": "Is largely independent",
        "B": "Needs some assistance with daily activities",
        "C": "Cannot safely live alone",
        "D": "Is mostly bed-bound or mobility-dependent",
    },
    "q4": {
        "A": "No rehabilitation needed",
        "B": "Needs outpatient physical, occupational, or speech therapy",
        "C": "Needs intensive inpatient rehabilitation",
        "D": "Rehabilitation needs are still unclear",
    },
    "q5": {
        "A": "Care is needed only during the daytime",
        "B": "Needs short-term recovery care",
        "C": "Needs long-term ongoing care",
        "D": "Needs end-of-life comfort care",
    },
}


def normalize_question_id(question_id: str) -> str:
    """Canonicalise a question id (trim + lowercase) for matrix lookups."""
    return str(question_id).strip().lower()


def normalize_option_id(option_id: str) -> str:
    """Canonicalise an option id (trim + uppercase) for matrix lookups."""
    return str(option_id).strip().upper()