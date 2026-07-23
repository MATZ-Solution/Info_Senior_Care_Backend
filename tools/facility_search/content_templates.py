"""
Phase 3 -- per-facility_type templates turning Layer 1 (infomary_facilities) +
Layer 2 (infomary_facility_detail.attributes) fields into the prose that gets
embedded and pushed to Qdrant (architecture docs, Section 4).

Two deliberate departures from the docs' worked examples in Section 4, both
argued explicitly rather than accidental:

1. Facility NAME is never included, per Section 5 ("Why Facility Names Are
   Never Embedded") -- shared generic words in names (e.g. "Family," "Care,"
   "Hospice") cause false-positive vector clustering unrelated to real
   similarity. Section 4's example sentences open with the name, but Section
   5's argued position wins over that prose; the name still lives in Supabase
   for display, it's just never fed to the embedding model.
2. city/state are also omitted here -- they are structured *filter* fields
   (Qdrant payload / Postgres), not semantic content; Section 4's own example
   sentences never mention them either, only ownership/scale/quality fields.

Each builder takes (facility: dict, attributes: dict) -> str and must never
raise for missing/null fields -- absent data is omitted from the sentence, not
rendered as "None". schemas.py is the source of truth for which attributes
keys can even exist per type.

Phase 11 -- All_State_Type_combined maps overall_rating and the shared "offers"
sub-object unconditionally for every type (schemas.py's _BASE_PROPERTIES), so
_shared_facts() below is folded into every builder, not just the ones with
their own type-specific fields.
"""

TYPE_LABELS = {
    "nursing_home": "nursing home",
    "home_health": "home health agency",
    "hospice": "hospice provider",
    "irf": "inpatient rehabilitation facility",
    "ltch": "long-term care hospital",
    "assisted_living": "assisted living facility",
    "icf_iid": "intermediate care facility",
    "home_care": "home care agency",
    "adult_day_care": "adult day care center",
    "behavioral_health": "behavioral health facility",
    "outpatient_rehab": "outpatient rehabilitation center",
    "hospital": "hospital",
    "dialysis_center": "dialysis center",
    "ambulatory_surgery_center": "ambulatory surgery center",
    "nursing_staffing_agency": "nursing staffing agency",
    "other_specialty": "specialty facility",
}

OWNERSHIP_ADJ = {
    "for_profit": "for-profit",
    "nonprofit": "nonprofit",
    "government": "government-operated",
}


def _opening(facility_type: str, facility: dict) -> str:
    label = TYPE_LABELS[facility_type]
    adj = OWNERSHIP_ADJ.get(facility.get("ownership_type"))
    first_word = adj if adj else label
    article = "An" if first_word[0].lower() in "aeiou" else "A"
    return f"{article} {adj} {label}" if adj else f"{article} {label}"


def _cert_year(facility: dict):
    d = facility.get("certification_date")
    return getattr(d, "year", None)


def _join_facts(opening: str, facts: list) -> str:
    parts = [opening] + [f for f in facts if f]
    return ". ".join(parts) + "."


# Phase 11 -- overall_rating + the shared "offers" sub-object are mapped
# unconditionally for every facility_type (schemas.py's _BASE_PROPERTIES), so
# every builder below folds these facts in, not just the types with nothing
# else. Absent/null values are simply omitted, never rendered as "None".
def _shared_facts(attributes: dict) -> list[str]:
    facts = []
    overall = attributes.get("overall_rating")
    if overall is not None:
        facts.append(f"Overall rating {overall}/5")
    offers = attributes.get("offers") or {}
    offered = [name.replace("_", " ") for name, val in offers.items() if val]
    if offered:
        facts.append(f"also offering {', '.join(offered)}")
    return facts


def _nursing_home(facility: dict, attributes: dict) -> str:
    opening = _opening("nursing_home", facility)
    beds = attributes.get("total_certified_beds")
    if beds is not None:
        opening += f" with {beds} certified beds"

    facts = list(_shared_facts(attributes))

    sub = [
        f"{label} {attributes[key]}/5"
        for key, label in (
            ("staffing_rating", "staffing"),
            ("health_inspection_rating", "health inspections"),
            ("quality_measure_rating", "quality measures"),
        )
        if attributes.get(key) is not None
    ]
    if sub:
        facts.append(", ".join(sub).capitalize())

    chain = attributes.get("chain_affiliation")
    if chain:
        facts.append(f"Chain affiliation: {chain}")

    assessment = attributes.get("staffing_level_assessment")
    if assessment:
        facts.append(f"Staffing level assessment: {assessment}")

    year = _cert_year(facility)
    if year:
        facts.append(f"certified since {year}")

    return _join_facts(opening, facts)


def _home_health(facility: dict, attributes: dict) -> str:
    opening = _opening("home_health", facility)
    facts = list(_shared_facts(attributes))

    service_fields = (
        ("offers_nursing_care", "nursing care"),
        ("offers_physical_therapy", "physical therapy"),
        ("offers_occupational_therapy", "occupational therapy"),
        ("offers_speech_therapy", "speech therapy"),
        ("offers_medical_social_services", "medical social services"),
        ("offers_home_health_aides", "home health aides"),
    )
    offered = [label for key, label in service_fields if attributes.get(key)]
    if offered:
        facts.append(f"offering {', '.join(offered)}")

    discharge = attributes.get("home_discharge_success")
    if discharge is not None:
        facts.append(f"home discharge success rate {discharge}")

    year = _cert_year(facility)
    if year:
        facts.append(f"certified since {year}")

    return _join_facts(opening, facts)


def _generic(facility_type: str):
    """
    Every type with no combined-source columns beyond the shared base
    (overall_rating/offers/source_extra) -- hospice, irf, and all Phase 11
    additions except nursing_home/home_health.
    """
    def build(facility: dict, attributes: dict) -> str:
        opening = _opening(facility_type, facility)
        facts = list(_shared_facts(attributes))
        year = _cert_year(facility)
        if year:
            facts.append(f"certified since {year}")
        return _join_facts(opening, facts)
    return build


def _nursing_staffing_agency(facility: dict, attributes: dict) -> str:
    """
    Explicit, not implied -- this is a B2B staffing vendor supplying nurses TO
    facilities, not somewhere a patient resides or receives care. Stated
    plainly in the generated content itself (the text the LLM actually sees
    and paraphrases from), so it can't get presented as a place someone can
    move into -- same honesty standard already applied to thin types.
    """
    opening = _opening("nursing_staffing_agency", facility)
    opening += " -- a staffing vendor that supplies nursing staff to other facilities, not a residence or direct-care location"
    facts = list(_shared_facts(attributes))
    year = _cert_year(facility)
    if year:
        facts.append(f"certified since {year}")
    return _join_facts(opening, facts)


# Historical only -- ltch is retired (seed.py), receives no rows going forward.
def _ltch(facility: dict, attributes: dict) -> str:
    opening = _opening("ltch", facility)
    beds = attributes.get("total_beds")
    if beds is not None:
        opening += f" with {beds} total beds"
    year = _cert_year(facility)
    facts = [f"certified since {year}"] if year else []
    return _join_facts(opening, facts)


CONTENT_BUILDERS = {
    "nursing_home": _nursing_home,
    "home_health": _home_health,
    "hospice": _generic("hospice"),
    "irf": _generic("irf"),
    "ltch": _ltch,
    "assisted_living": _generic("assisted_living"),
    "icf_iid": _generic("icf_iid"),
    "home_care": _generic("home_care"),
    "adult_day_care": _generic("adult_day_care"),
    "behavioral_health": _generic("behavioral_health"),
    "outpatient_rehab": _generic("outpatient_rehab"),
    "hospital": _generic("hospital"),
    "dialysis_center": _generic("dialysis_center"),
    "ambulatory_surgery_center": _generic("ambulatory_surgery_center"),
    "nursing_staffing_agency": _nursing_staffing_agency,
    "other_specialty": _generic("other_specialty"),
}


def build_content(facility_type: str, facility: dict, attributes: dict) -> str:
    try:
        builder = CONTENT_BUILDERS[facility_type]
    except KeyError:
        raise ValueError(f"no content builder registered for facility_type={facility_type!r}")
    return builder(facility, attributes)
