"""
Per-table field mappings for the 5 in-scope CMS source tables -- the Section 7
human-reviewed judgment calls (Layer 1 vs. Layer 2 placement, embedding-worthiness)
encoded as data. seed_mappings() loads these into infomary_source_field_mappings;
etl.py only ever reads that table, never this module directly, so the config-vs-
engine separation the docs call for is real.

Source table names and column names below are copied verbatim from a live
information_schema introspection, not retyped from memory -- several CMS column
headers were truncated by Postgres's 63-byte identifier limit when these tables
were first imported, so hand-transcribing them risks a silent off-by-a-few-bytes
mismatch. The ~13 Home Health "how often patients..." outcome-rate columns hit
this risk hardest (some end mid-word with an uncertain trailing space) and are
therefore discovered live at seed time (_discover_home_health_outcomes) rather
than hardcoded -- everything else here is short/complete and safe to hardcode.
"""
from database import get_db_connection
from logger import log_db, log_success

HOSPICE_TABLE = "Hospice Providers"
IRF_TABLE = "Informary_Inpatient Rehabilitation Facilities"
LTCH_TABLE = "Long_Term_hospitalcare"
HOME_HEALTH_TABLE = "Home Health Agencies"
NURSING_HOME_TABLE = "Nursing Homes — Provider Information"  # em dash (U+2014), confirmed live


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


def _identity_block(table, facility_type, ccn_col, name_col, addr1_col, addr2_col="Address Line 2",
                     has_addr2=True, has_county=True, has_cms_region=True):
    """The Layer-1 shape shared by Hospice/IRF/LTCH (and re-used partially by others)."""
    rows = [
        # CCN is NOT facility_id -- it's a plain, unvalidated audit column.
        # facility_id is computed separately in etl.py (uuid5 of this value, or a
        # fallback natural key if this is blank) -- see etl.py's _compute_facility_id.
        _m(table, ccn_col, "source_identifier", "facilities", "to_text", facility_type),
        _m(table, name_col, "name", "facilities", "to_text", facility_type, is_required=True),
        _m(table, addr1_col, "address_line1", "facilities", "to_text", facility_type),
        _m(table, "City/Town", "city", "facilities", "to_text", facility_type),
        _m(table, "State", "state", "facilities", "to_text", facility_type),
        _m(table, "ZIP Code", "zip_code", "facilities", "zero_pad_5", facility_type),
        _m(table, "Telephone Number", "phone", "facilities", "to_text", facility_type),
        _m(table, "Ownership Type", "ownership_type", "facilities", "normalize_ownership", facility_type),
        _m(table, "Certification Date", "certification_date", "facilities", "parse_cms_date", facility_type),
    ]
    if has_addr2:
        rows.append(_m(table, addr2_col, "address_line2", "facilities", "to_text", facility_type))
    if has_county:
        rows.append(_m(table, "County/Parish", "county", "facilities", "to_text", facility_type))
    if has_cms_region:
        rows.append(_m(table, "CMS Region", "cms_region", "facilities", "to_int", facility_type))
    return rows


HOSPICE_MAPPINGS = _identity_block(
    HOSPICE_TABLE, "hospice", "CMS Certification Number (CCN)", "Facility Name", "Address Line 1",
)

IRF_MAPPINGS = _identity_block(
    IRF_TABLE, "irf", "CMS Certification Number (CCN)", "Provider Name", "Address Line 1",
)

LTCH_MAPPINGS = _identity_block(
    LTCH_TABLE, "ltch", "CMS Certification Number (CCN)", "Provider Name", "Address Line 1",
) + [
    _m(LTCH_TABLE, "Total Number of Beds", "total_beds", "facility_detail", "to_int", "ltch"),
]

# Home Health Agencies has a different shape: single "Address" column, no
# County/CMS Region columns at all (those stay NULL for this type).
HOME_HEALTH_STATIC_MAPPINGS = [
    _m(HOME_HEALTH_TABLE, "CMS Certification Number (CCN)", "source_identifier", "facilities", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "Provider Name", "name", "facilities", "to_text", "home_health", is_required=True),
    _m(HOME_HEALTH_TABLE, "Address", "address_line1", "facilities", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "City/Town", "city", "facilities", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "State", "state", "facilities", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "ZIP Code", "zip_code", "facilities", "zero_pad_5", "home_health"),
    _m(HOME_HEALTH_TABLE, "Telephone Number", "phone", "facilities", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "Type of Ownership", "ownership_type", "facilities", "normalize_ownership", "home_health"),
    _m(HOME_HEALTH_TABLE, "Certification Date", "certification_date", "facilities", "parse_cms_date", "home_health"),
    _m(HOME_HEALTH_TABLE, "Offers Nursing Care Services", "services.nursing", "facility_detail", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "Offers Physical Therapy Services", "services.physical_therapy", "facility_detail", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "Offers Occupational Therapy Services", "services.occupational_therapy", "facility_detail", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "Offers Speech Pathology Services", "services.speech_pathology", "facility_detail", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "Offers Medical Social Services", "services.medical_social", "facility_detail", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "Offers Home Health Aide Services", "services.home_health_aide", "facility_detail", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "Quality of patient care star rating", "quality_star_rating", "facility_detail", "to_float", "home_health"),
    _m(HOME_HEALTH_TABLE, "DTC Performance Categorization", "discharge_to_community_category", "facility_detail", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "PPR Performance Categorization", "readmission_category", "facility_detail", "to_text", "home_health"),
    _m(HOME_HEALTH_TABLE, "PPH Performance Categorization", "preventable_hospitalization_category", "facility_detail", "to_text", "home_health"),
]

# Columns already covered above, or deliberately excluded (Numerator/Denominator/
# Footnote noise, or the truncated/optional Medicare-spend-per-episode fields) --
# used to compute what's left over for _discover_home_health_outcomes.
_HOME_HEALTH_EXCLUDED_PREFIXES = ("Numerator for", "Denominator for", "Footnote")
_HOME_HEALTH_ALREADY_MAPPED = {m["source_column"] for m in HOME_HEALTH_STATIC_MAPPINGS}


async def _discover_home_health_outcomes(conn):
    """
    The ~13 "How often patients got better at X" rate columns -- discovered live
    (not hardcoded) because several of their exact names are truncated at
    Postgres's 63-byte identifier limit with an uncertain trailing space, and a
    mistyped source_column would silently fail to match any row data rather than
    erroring loudly. Anything not already statically mapped and not matching the
    Numerator/Denominator/Footnote noise prefixes is one of these outcome columns.
    """
    cols = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name = $1 ORDER BY ordinal_position",
        HOME_HEALTH_TABLE,
    )
    rows = []
    for c in cols:
        name = c["column_name"]
        if name in _HOME_HEALTH_ALREADY_MAPPED:
            continue
        if any(name.startswith(p) for p in _HOME_HEALTH_EXCLUDED_PREFIXES):
            continue
        if name in ("How much Medicare spends on an episode of care at this agency, ",):
            continue
        slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")
        while "__" in slug:
            slug = slug.replace("__", "_")
        rows.append(_m(HOME_HEALTH_TABLE, name, f"outcomes.{slug[:40]}", "facility_detail", "to_text", "home_health"))
    return rows


NURSING_HOME_MAPPINGS = [
    _m(NURSING_HOME_TABLE, "CMS Certification Number (CCN)", "source_identifier", "facilities", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Provider Name", "name", "facilities", "to_text", "nursing_home", is_required=True),
    _m(NURSING_HOME_TABLE, "Provider Address", "address_line1", "facilities", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "City/Town", "city", "facilities", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "State", "state", "facilities", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "ZIP Code", "zip_code", "facilities", "zero_pad_5", "nursing_home"),
    _m(NURSING_HOME_TABLE, "County/Parish", "county", "facilities", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Telephone Number", "phone", "facilities", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Ownership Type", "ownership_type", "facilities", "normalize_ownership", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Date First Approved to Provide Medicare and Medicaid Services", "certification_date", "facilities", "parse_cms_date", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Latitude", "latitude", "facilities", "to_float", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Longitude", "longitude", "facilities", "to_float", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Provider Type", "provider_type", "facility_detail", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Number of Certified Beds", "certified_beds", "facility_detail", "to_int", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Average Number of Residents per Day", "avg_residents_per_day", "facility_detail", "to_float", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Overall Rating", "ratings.overall", "facility_detail", "to_int", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Health Inspection Rating", "ratings.health_inspection", "facility_detail", "to_int", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Staffing Rating", "ratings.staffing", "facility_detail", "to_int", "nursing_home"),
    _m(NURSING_HOME_TABLE, "QM Rating", "ratings.qm", "facility_detail", "to_int", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Special Focus Status", "special_focus_status", "facility_detail", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Abuse Icon", "abuse_icon", "facility_detail", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Continuing Care Retirement Community", "ccrc", "facility_detail", "to_text", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Reported RN Staffing Hours per Resident per Day", "staffing_hours.rn", "facility_detail", "to_float", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Reported LPN Staffing Hours per Resident per Day", "staffing_hours.lpn", "facility_detail", "to_float", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Reported Nurse Aide Staffing Hours per Resident per Day", "staffing_hours.nurse_aide", "facility_detail", "to_float", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Total Number of Penalties", "total_penalties", "facility_detail", "to_int", "nursing_home"),
    _m(NURSING_HOME_TABLE, "Number of Fines", "number_of_fines", "facility_detail", "to_int", "nursing_home"),
]


async def seed_mappings():
    """
    Replaces (not merely appends) each in-scope source_table's mapping rows.
    Append-only seeding would leave stale rows behind whenever a mapping in this
    module changes shape -- e.g. when CCN's target moved from facility_id to
    source_identifier, an append-only seed would have left the old facility_id
    row in place alongside the new one, and etl.py would have tried to populate
    facility_id from both.
    """
    log_db("Seeding source_field_mappings...")
    async with get_db_connection() as conn:
        home_health_outcomes = await _discover_home_health_outcomes(conn)
        all_rows = (
            HOSPICE_MAPPINGS + IRF_MAPPINGS + LTCH_MAPPINGS
            + HOME_HEALTH_STATIC_MAPPINGS + home_health_outcomes
            + NURSING_HOME_MAPPINGS
        )
        source_tables = {m["source_table"] for m in all_rows}
        for source_table in source_tables:
            await conn.execute(
                "DELETE FROM infomary_source_field_mappings WHERE source_table = $1", source_table,
            )
        for m in all_rows:
            await conn.execute(
                "INSERT INTO infomary_source_field_mappings "
                "(source_table, source_column, target_field, target_layer, transform_fn, is_required, facility_type) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                m["source_table"], m["source_column"], m["target_field"], m["target_layer"],
                m["transform_fn"], m["is_required"], m["facility_type"],
            )
        log_success(f"source_field_mappings seeded ({len(all_rows)} rows, "
                    f"{len(home_health_outcomes)} home_health outcome columns discovered live)")
