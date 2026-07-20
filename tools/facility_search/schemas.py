"""
One JSON Schema per facility_type, validating infomary_facility_detail.attributes
before insert. JSONB has no native type safety -- this is the ETL-layer substitute
(architecture docs, Section 3.2).
"""

_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}

_LTCH = {
    "type": "object",
    "properties": {
        "total_beds": {"type": ["integer", "null"]},
    },
    "additionalProperties": False,
}

_HOME_HEALTH = {
    "type": "object",
    "properties": {
        "services": {
            "type": "object",
            "properties": {
                "nursing": {"type": ["string", "null"]},
                "physical_therapy": {"type": ["string", "null"]},
                "occupational_therapy": {"type": ["string", "null"]},
                "speech_pathology": {"type": ["string", "null"]},
                "medical_social": {"type": ["string", "null"]},
                "home_health_aide": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "quality_star_rating": {"type": ["number", "null"]},
        "discharge_to_community_category": {"type": ["string", "null"]},
        "readmission_category": {"type": ["string", "null"]},
        "preventable_hospitalization_category": {"type": ["string", "null"]},
        "outcomes": {
            "type": "object",
            "additionalProperties": {"type": ["string", "null"]},
        },
    },
    "additionalProperties": False,
}

_NURSING_HOME = {
    "type": "object",
    "properties": {
        "provider_type": {"type": ["string", "null"]},
        "certified_beds": {"type": ["integer", "null"]},
        "avg_residents_per_day": {"type": ["number", "null"]},
        "ratings": {
            "type": "object",
            "properties": {
                "overall": {"type": ["integer", "null"]},
                "health_inspection": {"type": ["integer", "null"]},
                "staffing": {"type": ["integer", "null"]},
                "qm": {"type": ["integer", "null"]},
            },
            "additionalProperties": False,
        },
        "special_focus_status": {"type": ["string", "null"]},
        "abuse_icon": {"type": ["string", "null"]},
        "ccrc": {"type": ["string", "null"]},
        "staffing_hours": {
            "type": "object",
            "properties": {
                "rn": {"type": ["number", "null"]},
                "lpn": {"type": ["number", "null"]},
                "nurse_aide": {"type": ["number", "null"]},
            },
            "additionalProperties": False,
        },
        "total_penalties": {"type": ["integer", "null"]},
        "number_of_fines": {"type": ["integer", "null"]},
    },
    "additionalProperties": False,
}

SCHEMAS = {
    "hospice": _EMPTY,
    "irf": _EMPTY,
    "ltch": _LTCH,
    "home_health": _HOME_HEALTH,
    "nursing_home": _NURSING_HOME,
}
