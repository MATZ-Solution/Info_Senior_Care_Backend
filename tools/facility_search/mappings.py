"""
Phase 11 -- field mappings for All_State_Type_combined, the single active
source going forward. The 5 original CMS tables (Hospice Providers,
Informary_Inpatient Rehabilitation Facilities, Long_Term_hospitalcare, Home
Health Agencies, Nursing Homes -- Provider Information) and their mapping
rows are retired -- see the Phase 11 plan for the one-off
`DELETE FROM infomary_source_field_mappings WHERE source_table IN (...)`
cleanup for those 5 table names, run once at migration cutover. Nothing in
this module references them anymore; the raw tables themselves stay
untouched in Supabase as historical record, just unread.

seed_mappings() loads COMBINED_MAPPINGS into infomary_source_field_mappings;
etl.py only ever reads that table, never this module directly, so the
config-vs-engine separation the docs call for is real.

Column names/casing below are confirmed via a real information_schema
introspection of All_State_Type_combined, not retyped from memory.
"""
from database import get_db_connection
from logger import log_db, log_success

COMBINED_TABLE = "All_State_Type_combined"


def _m(source_table, source_column, target_field, target_layer, transform_fn, facility_type, is_required=False):
    return {
        "source_table": source_table,
        "source_column": source_column,
        "target_field": target_field,
        "target_layer": target_layer,
        "transform_fn": transform_fn,
        "is_required": is_required,
        "facility_type": facility_type,
    }


def _c(source_column, target_field, target_layer, transform_fn, is_required=False):
    return _m(COMBINED_TABLE, source_column, target_field, target_layer, transform_fn, None, is_required)


# facility_type_category (exact live strings) -> type_key. This is what
# etl.py's _process_combined_table uses to resolve each ROW's type -- the
# combined table holds all 15 active types in one table, each row carrying
# its own category, unlike the old design where type was fixed per whole
# table.
#
# Per your decision: 15 of the 16 real categories are included; only Health
# Maintenance Organization is excluded (not a physical care location). A
# category present in the live data but absent from BOTH this dict AND
# KNOWN_EXCLUDED_CATEGORIES is a genuine surprise (an unreviewed 17th
# category) -- etl.py logs this distinctly from a normal, expected exclusion.
FACILITY_TYPE_CATEGORY_RESOLUTION: dict[str, str] = {
    "Nursing Home / Skilled Nursing Facility": "nursing_home",
    "Home Health Agency": "home_health",
    "Hospice": "hospice",
    "Rehabilitation - Inpatient": "irf",
    "Residential Care / Assisted Living": "assisted_living",
    "Intermediate Care Facility (ICF/IID)": "icf_iid",
    "Home Care Agency": "home_care",
    "Adult Day Care": "adult_day_care",
    "Mental/Behavioral Health Facility": "behavioral_health",
    "Rehabilitation - Outpatient": "outpatient_rehab",
    "Hospital": "hospital",
    "Dialysis Center": "dialysis_center",
    "Ambulatory Surgery Center": "ambulatory_surgery_center",
    "Nursing Staffing Agency": "nursing_staffing_agency",
    "Other/Unspecified/Specialty": "other_specialty",
    # "Health Maintenance Organization" deliberately NOT mapped -- the one
    # confirmed exclusion. Falls through to the "unresolved, skip+count"
    # branch in etl.py's Pass 1, same mechanism as any genuinely unrecognized
    # category, but tagged as an expected exclusion (see KNOWN_EXCLUDED_
    # CATEGORIES below), not a surprise one.
}

KNOWN_EXCLUDED_CATEGORIES: frozenset[str] = frozenset({"Health Maintenance Organization"})
KNOWN_CATEGORIES: frozenset[str] = frozenset(FACILITY_TYPE_CATEGORY_RESOLUTION.keys()) | KNOWN_EXCLUDED_CATEGORIES


COMBINED_MAPPINGS: list[dict] = [
    # -----------------------------------------------------------------
    # Layer 1 -- facilities (identity / filterable). facility_type is None
    # for every row here -- this column is purely informational (never used
    # to select mappings at query time, see etl.py's mapping SELECT), and
    # DROP NOT NULL (schema.py) makes this legal.
    # -----------------------------------------------------------------
    _c("ccn", "source_identifier", "facilities", "to_text"),
    _c("name", "name", "facilities", "to_text", is_required=True),
    _c("legal_business_name", "legal_business_name", "facilities", "to_text"),
    _c("ownership_type", "ownership_type", "facilities", "normalize_ownership"),
    _c("address", "address_line1", "facilities", "to_text"),
    _c("city", "city", "facilities", "title_case"),
    _c("state", "state", "facilities", "upper_trim"),
    _c("zip_code", "zip_code", "facilities", "zero_pad_5"),
    _c("county", "county", "facilities", "title_case"),
    _c("phone", "phone", "facilities", "to_text"),
    _c("email", "email", "facilities", "to_text"),
    _c("facility_subtype", "facility_subtype", "facilities", "to_text"),
    _c("cms_region", "cms_region", "facilities", "to_int"),
    _c("certification_date", "certification_date", "facilities", "parse_cms_date"),
    _c("latitude", "latitude", "facilities", "to_float"),
    _c("longitude", "longitude", "facilities", "to_float"),

    # -----------------------------------------------------------------
    # Layer 2 -- facility_detail.attributes -- shared across every type
    # (schemas.py's _BASE_PROPERTIES: overall_rating/offers/source_extra)
    # -----------------------------------------------------------------
    _c("overall_rating", "overall_rating", "facility_detail", "to_float"),
    _c("extra_attributes", "source_extra", "facility_detail", "passthrough_json"),
    _c("offers_alzheimer_dementia_care", "offers.alzheimer_dementia_care", "facility_detail", "to_text"),
    _c("offers_adult_day_care", "offers.adult_day_care", "facility_detail", "to_text"),
    _c("offers_respite_care", "offers.respite_care", "facility_detail", "to_text"),
    _c("offers_home_care_services", "offers.home_care_services", "facility_detail", "to_text"),
    _c("offers_iv_therapy", "offers.iv_therapy", "facility_detail", "to_text"),
    _c("offers_pain_management", "offers.pain_management", "facility_detail", "to_text"),
    _c("offers_medical_equipment_supply", "offers.medical_equipment_supply", "facility_detail", "to_text"),

    # -----------------------------------------------------------------
    # Layer 2 -- nursing_home-specific (blank/None for non-nursing-home
    # rows; to_int/to_text/to_float already return None on blank input,
    # so this is safe to map unconditionally for every row)
    # -----------------------------------------------------------------
    _c("nh_total_certified_beds", "total_certified_beds", "facility_detail", "to_int"),
    _c("nh_chain_affiliation", "chain_affiliation", "facility_detail", "to_text"),
    _c("nh_health_inspection_star_rating", "health_inspection_rating", "facility_detail", "to_float"),
    _c("nh_staffing_star_rating", "staffing_rating", "facility_detail", "to_float"),
    _c("nh_quality_measure_star_rating", "quality_measure_rating", "facility_detail", "to_float"),
    _c("nh_staffing_level_assessment", "staffing_level_assessment", "facility_detail", "to_text"),

    # -----------------------------------------------------------------
    # Layer 2 -- home_health-specific
    # -----------------------------------------------------------------
    _c("hh_provides_nursing_care", "offers_nursing_care", "facility_detail", "to_text"),
    _c("hh_provides_physical_therapy", "offers_physical_therapy", "facility_detail", "to_text"),
    _c("hh_provides_occupational_therapy", "offers_occupational_therapy", "facility_detail", "to_text"),
    _c("hh_provides_speech_therapy", "offers_speech_therapy", "facility_detail", "to_text"),
    _c("hh_provides_medical_social_services", "offers_medical_social_services", "facility_detail", "to_text"),
    _c("hh_provides_home_health_aides", "offers_home_health_aides", "facility_detail", "to_text"),
    _c("hh_home_discharge_success", "home_discharge_success", "facility_detail", "to_float"),
]


async def seed_mappings():
    """
    Replaces (not merely appends) source_table's mapping rows. Append-only
    seeding would leave stale rows behind whenever a mapping in this module
    changes shape.
    """
    log_db("Seeding source_field_mappings...")
    async with get_db_connection() as conn:
        await conn.execute(
            "DELETE FROM infomary_source_field_mappings WHERE source_table = $1", COMBINED_TABLE,
        )
        for m in COMBINED_MAPPINGS:
            await conn.execute(
                "INSERT INTO infomary_source_field_mappings "
                "(source_table, source_column, target_field, target_layer, transform_fn, is_required, facility_type) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                m["source_table"], m["source_column"], m["target_field"], m["target_layer"],
                m["transform_fn"], m["is_required"], m["facility_type"],
            )
        log_success(f"source_field_mappings seeded ({len(COMBINED_MAPPINGS)} rows for {COMBINED_TABLE})")
