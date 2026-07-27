"""
One JSON Schema per facility_type, validating infomary_facility_detail.attributes
before insert. JSONB has no native type safety -- this is the ETL-layer substitute
(architecture docs, Section 3.2).

Phase 11 -- All_State_Type_combined maps three fields unconditionally for every
row regardless of facility_type: overall_rating, the shared "offers" sub-object,
and source_extra (an opaque catch-all). Since every schema below uses
additionalProperties: False, all three MUST appear in every type's properties or
every row of any type lacking one of them would fail schema validation and be
silently rejected -- this bit the original per-type-only design (_EMPTY schemas
for hospice/irf) the moment a single combined-source column got mapped
unconditionally. _schema() bakes these three in for every type so this can't be
missed again when a new type is added.

_LTCH is retained only for historical reference (ltch is retired -- see seed.py --
and receives zero rows from the combined source going forward); it is
deliberately NOT rewritten to the shared-base shape, since nothing writes to it
anymore.
"""

_OFFERS = {
    "type": "object",
    "properties": {
        "alzheimer_dementia_care": {"type": ["string", "null"]},
        "adult_day_care": {"type": ["string", "null"]},
        "respite_care": {"type": ["string", "null"]},
        "home_care_services": {"type": ["string", "null"]},
        "iv_therapy": {"type": ["string", "null"]},
        "pain_management": {"type": ["string", "null"]},
        "medical_equipment_supply": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

_BASE_PROPERTIES = {
    "overall_rating": {"type": ["number", "null"]},
    "offers": _OFFERS,
    "source_extra": {},  # opaque catch-all, deliberately unvalidated -- any shape allowed
}


def _schema(extra_properties: dict | None = None) -> dict:
    properties = dict(_BASE_PROPERTIES)
    if extra_properties:
        properties.update(extra_properties)
    return {"type": "object", "properties": properties, "additionalProperties": False}


_NURSING_HOME = _schema({
    "total_certified_beds": {"type": ["integer", "null"]},
    "chain_affiliation": {"type": ["string", "null"]},
    "health_inspection_rating": {"type": ["number", "null"]},
    "staffing_rating": {"type": ["number", "null"]},
    "quality_measure_rating": {"type": ["number", "null"]},
    "staffing_level_assessment": {"type": ["string", "null"]},
})

_HOME_HEALTH = _schema({
    "offers_nursing_care": {"type": ["string", "null"]},
    "offers_physical_therapy": {"type": ["string", "null"]},
    "offers_occupational_therapy": {"type": ["string", "null"]},
    "offers_speech_therapy": {"type": ["string", "null"]},
    "offers_medical_social_services": {"type": ["string", "null"]},
    "offers_home_health_aides": {"type": ["string", "null"]},
    "home_discharge_success": {"type": ["number", "null"]},
})

# hospice, irf, and every Phase 11 type with no type-specific combined-source
# columns of its own -- just the shared base (overall_rating/offers/source_extra).
_GENERIC = _schema()

# Historical only -- ltch is retired (seed.py), receives no rows going forward.
_LTCH = {
    "type": "object",
    "properties": {
        "total_beds": {"type": ["integer", "null"]},
    },
    "additionalProperties": False,
}

SCHEMAS = {
    "nursing_home": _NURSING_HOME,
    "home_health": _HOME_HEALTH,
    "hospice": _GENERIC,
    "irf": _GENERIC,
    "ltch": _LTCH,
    "assisted_living": _GENERIC,
    "icf_iid": _GENERIC,
    "home_care": _GENERIC,
    "adult_day_care": _GENERIC,
    "behavioral_health": _GENERIC,
    "outpatient_rehab": _GENERIC,
    "hospital": _GENERIC,
    "dialysis_center": _GENERIC,
    "ambulatory_surgery_center": _GENERIC,
    "nursing_staffing_agency": _GENERIC,
    "other_specialty": _GENERIC,
}
