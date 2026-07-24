"""
Phase 1 seed data -- the facility types and their common aliases.

infomary_known_values is intentionally left empty here: it's populated in Phase 2
from real ingested city/state values. infomary_source_field_mappings is populated
by mappings.py::seed_mappings(), not here.

Phase 11 -- All_State_Type_combined added 11 new types (assisted_living, icf_iid,
home_care, adult_day_care, behavioral_health, outpatient_rehab, hospital,
dialysis_center, ambulatory_surgery_center, nursing_staffing_agency,
other_specialty -- 15 of the source's 16 facility_type_category values are
included, only Health Maintenance Organization is excluded) and retired ltch
(absent from the new source, not reintroduced). ltch's row is deactivated, not
deleted -- it stays as historical record, consistent with the retired source
tables themselves. Note is_active is NOT currently read anywhere (fuzzy_match.py's
alias lookup doesn't join back to facility_types), so this is documentation of
intent today, not an active filter -- ltch simply has zero rows post-migration.
"""
from database import get_db_connection
from logger import log_db, log_success

FACILITY_TYPES = [
    {
        "type_key": "nursing_home",
        "display_name": "Nursing Home",
        "aliases": ["nursing home", "skilled nursing facility", "snf", "nursing facility"],
    },
    {
        "type_key": "home_health",
        "display_name": "Home Health Agency",
        "aliases": ["home health agency", "home health care", "home care agency", "in-home care", "home health"],
    },
    {
        "type_key": "hospice",
        "display_name": "Hospice",
        "aliases": ["hospice", "hospice care", "hospice provider"],
    },
    {
        "type_key": "irf",
        "display_name": "Inpatient Rehabilitation Facility",
        "aliases": ["inpatient rehabilitation facility", "irf", "rehab facility", "rehabilitation hospital", "inpatient rehab"],
    },
    {
        "type_key": "assisted_living",
        "display_name": "Assisted Living",
        "aliases": [
            "assisted living", "residential care facility", "personal care home",
            "home for the aged", "board and care home", "adult family care",
            "adult residential care home", "community residence",
        ],
    },
    {
        "type_key": "icf_iid",
        "display_name": "Intermediate Care Facility (ICF/IID)",
        "aliases": ["icf/iid", "intermediate care facility", "icf iid", "icfiid"],
    },
    {
        "type_key": "home_care",
        "display_name": "Home Care Agency",
        "aliases": [
            "home care", "home care service provider", "individual home care provider",
            "in-home support services", "home and community based services",
            "residential service agency", "personal care/homemaker", "personal care homemaker",
        ],
    },
    {
        "type_key": "adult_day_care",
        "display_name": "Adult Day Care",
        "aliases": ["adult day care", "adult day health care", "adult daycare"],
    },
    {
        "type_key": "behavioral_health",
        "display_name": "Behavioral Health Facility",
        "aliases": [
            "mental health hospital", "mental health homes", "mental health residential home",
            "medication-assisted treatment", "behavioral health facility", "mental health facility",
        ],
    },
    {
        "type_key": "outpatient_rehab",
        "display_name": "Outpatient Rehabilitation",
        "aliases": [
            "rehabilitation center", "rehabilitation clinic", "rehabilitation agency",
            "cardiac rehabilitation", "outpatient rehab", "outpatient rehabilitation",
        ],
    },
    {
        "type_key": "hospital",
        "display_name": "Hospital",
        "aliases": ["hospital", "hospital (state license)"],
    },
    {
        "type_key": "dialysis_center",
        "display_name": "Dialysis Center",
        "aliases": [
            "dialysis center", "dialysis center (state license)",
            "end-stage renal dialysis", "end stage renal dialysis",
        ],
    },
    {
        "type_key": "ambulatory_surgery_center",
        "display_name": "Ambulatory Surgery Center",
        "aliases": ["ambulatory surgery center", "ambulatory surgical center", "ambulatory surgery"],
    },
    {
        "type_key": "nursing_staffing_agency",
        "display_name": "Nursing Staffing Agency",
        "aliases": ["nursing pool", "nursing staffing agency", "nursing staffing pool"],
    },
    {
        "type_key": "other_specialty",
        "display_name": "Other/Specialty Facility",
        "aliases": ["other", "specialty facility", "unspecified facility"],
    },
]

# Retired -- absent from All_State_Type_combined, not reintroduced. Row is
# deactivated (see seed_facility_types), not deleted.
RETIRED_TYPE_KEYS = ["ltch"]


async def seed_facility_types():
    log_db("Seeding facility types + aliases...")
    async with get_db_connection() as conn:
        for ft in FACILITY_TYPES:
            await conn.execute(
                "INSERT INTO infomary_facility_types (type_key, display_name) VALUES ($1, $2) "
                "ON CONFLICT (type_key) DO NOTHING",
                ft["type_key"], ft["display_name"],
            )
            for alias in ft["aliases"]:
                await conn.execute(
                    "INSERT INTO infomary_facility_type_aliases (type_key, alias) VALUES ($1, $2) "
                    "ON CONFLICT (type_key, alias) DO NOTHING",
                    ft["type_key"], alias,
                )
        for type_key in RETIRED_TYPE_KEYS:
            await conn.execute(
                "UPDATE infomary_facility_types SET is_active = FALSE WHERE type_key = $1", type_key,
            )
        log_success(f"Facility types seeded ({len(FACILITY_TYPES)} types, {len(RETIRED_TYPE_KEYS)} retired)")
