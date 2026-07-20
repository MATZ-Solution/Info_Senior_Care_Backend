"""
Phase 1 seed data — the 5 known facility types and their common aliases.

infomary_known_values and infomary_source_field_mappings are intentionally left empty here:
known_values is populated in Phase 2 from real ingested city/state values, and
source_field_mappings is populated once the per-table column mappings are reviewed.
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
        "type_key": "ltch",
        "display_name": "Long-Term Care Hospital",
        "aliases": ["long term care hospital", "ltch", "ltach", "long-term acute care hospital"],
    },
]


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
        log_success(f"Facility types seeded ({len(FACILITY_TYPES)} types)")
