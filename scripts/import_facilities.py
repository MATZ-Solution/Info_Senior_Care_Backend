# """
# Idempotent facility data importer.

# Usage:
#     python3 -m scripts.import_facilities /path/to/data_with_uuid.csv

# Design goals (per product requirement -- "future file add karen to usme
# duplicates check karke insert karen"):
#   1. Re-running this script on the SAME file must NOT create duplicate rows.
#   2. Importing a NEW file that overlaps with existing facilities must UPDATE
#      those existing rows (fresher rating, fresher bed count, etc), not
#      duplicate them.
#   3. Matching priority: `ccn` when present (reliable CMS identifier),
#      otherwise `dedup_hash` (normalized name+address+zip+state -- see
#      app/services/dedup.py, the single shared source of truth for this).
#   4. The script must be safe to abort and re-run (no partial-batch
#      corruption) -- each batch is one transaction.

# This intentionally uses synchronous psycopg2 (not the app's async stack) --
# this is an offline admin/ops script, not a request-handling code path, and
# psycopg2's execute_values gives us simple, fast, well-understood batch
# upserts with RETURNING support to report accurate insert/update counts.
# """
# import argparse
# import csv
# import re
# import sys
# import uuid
# from datetime import datetime, timezone
# from pathlib import Path
# from typing import Optional

# import psycopg2
# from psycopg2.extras import execute_values, Json

# sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# from app.core.config import settings  # noqa: E402
# from app.services.dedup import compute_dedup_hash  # noqa: E402

# BATCH_SIZE = 500

# CORE_COLUMNS = [
#     "ccn", "name", "facility_type", "facility_type_category", "legal_business_name",
#     "ownership_type", "address", "city", "state", "zip_code", "county", "phone", "email",
#     "facility_subtype", "operating_status", "closed_date", "certification_date",
#     "latitude", "longitude", "bed_count", "secure_memory_care_beds", "overall_rating",
#     "cms_region", "specialty_notes", "source_state_abbr",
#     "data_source", "source_file", "schema_version", "load_timestamp",
# ]

# NH_COLUMNS = [
#     "nh_total_certified_beds", "nh_average_daily_residents", "nh_chain_affiliation",
#     "nh_ccrc", "nh_abuse_complaint", "nh_special_focus_facility", "nh_sprinkler_system",
#     "nh_health_inspection_star_rating", "nh_staffing_star_rating",
#     "nh_quality_measure_star_rating", "nh_total_nursing_hours_per_resident_day",
#     "nh_rn_hours_per_resident_day", "nh_lpn_hours_per_resident_day",
#     "nh_cna_hours_per_resident_day", "nh_pt_hours_per_resident_day",
#     "nh_staffing_level_assessment", "nh_total_nursing_staff_turnover_pct",
#     "nh_rn_turnover_pct", "nh_administrators_left_12mo", "nh_staff_stability",
#     "nh_health_deficiencies_latest", "nh_health_deficiency_severity_score",
#     "nh_weighted_health_inspection_score", "nh_number_of_fines", "nh_total_fines_usd",
#     "nh_medicare_payment_denials", "nh_total_penalties", "nh_infection_control_citations",
#     "nh_penalty_summary",
# ]

# HH_COLUMNS = [
#     "hh_provides_nursing_care", "hh_provides_physical_therapy",
#     "hh_provides_occupational_therapy", "hh_provides_speech_therapy",
#     "hh_provides_medical_social_services", "hh_provides_home_health_aides",
#     "hh_improved_walking_mobility_pct", "hh_improved_getting_out_of_bed_pct",
#     "hh_improved_bathing_ability_pct", "hh_improved_breathing_pct",
#     "hh_improved_taking_medications_pct", "hh_developed_bedsores_pct",
#     "hh_falls_major_injury_pct", "hh_started_care_on_time_pct",
#     "hh_medication_issues_fixed_on_time_pct", "hh_functional_ability_discharge_score",
#     "hh_info_shared_with_doctor_pct", "hh_info_shared_with_family_pct",
#     "hh_home_discharge_success", "hh_hospital_readmission_rate",
#     "hh_avoidable_hospitalizations", "hh_medicare_cost_vs_national_avg",
# ]

# SERVICE_COLUMNS = [
#     "offers_alzheimer_dementia_care", "offers_hospice_care", "offers_ventilator_care",
#     "offers_psychiatric_care", "offers_substance_abuse_treatment", "offers_hiv_care",
#     "offers_rehab_services", "offers_adult_day_care", "offers_respite_care",
#     "offers_home_care_services", "offers_traumatic_brain_injury_care",
#     "offers_iv_therapy", "offers_pain_management", "offers_medical_equipment_supply",
# ]

# INT_FIELDS = {
#     "bed_count", "secure_memory_care_beds", "cms_region",
#     "nh_total_certified_beds", "nh_number_of_fines",
#     "nh_medicare_payment_denials", "nh_total_penalties",
#     "nh_infection_control_citations", "nh_health_deficiencies_latest",
#     "nh_administrators_left_12mo",
# }
# FLOAT_FIELDS = {
#     "latitude", "longitude", "overall_rating", "nh_average_daily_residents",
#     "nh_health_inspection_star_rating", "nh_staffing_star_rating",
#     "nh_quality_measure_star_rating", "nh_total_nursing_hours_per_resident_day",
#     "nh_rn_hours_per_resident_day", "nh_lpn_hours_per_resident_day",
#     "nh_cna_hours_per_resident_day", "nh_pt_hours_per_resident_day",
#     "nh_total_nursing_staff_turnover_pct", "nh_rn_turnover_pct",
#     "nh_health_deficiency_severity_score", "nh_weighted_health_inspection_score",
#     "nh_total_fines_usd", "hh_improved_walking_mobility_pct",
#     "hh_improved_getting_out_of_bed_pct", "hh_improved_bathing_ability_pct",
#     "hh_improved_breathing_pct", "hh_improved_taking_medications_pct",
#     "hh_developed_bedsores_pct", "hh_falls_major_injury_pct",
#     "hh_started_care_on_time_pct", "hh_medication_issues_fixed_on_time_pct",
#     "hh_functional_ability_discharge_score", "hh_info_shared_with_doctor_pct",
#     "hh_info_shared_with_family_pct", "hh_home_discharge_success",
#     "hh_hospital_readmission_rate", "hh_avoidable_hospitalizations",
# }


# def clean(value: Optional[str]) -> Optional[str]:
#     if value is None:
#         return None
#     value = value.strip()
#     return value if value else None


# def clean_zip(value: Optional[str]) -> Optional[str]:
#     """
#     Source data has inconsistent ZIP formatting (e.g. '20774 \u2013 1474' with
#     an en-dash and stray spaces instead of '20774-1474'). Normalize to a
#     plain 5 or 5+4 digit hyphenated format so zip-based search/filtering
#     works consistently regardless of which source file a row came from.
#     """
#     value = clean(value)
#     if value is None:
#         return None
#     value = value.replace("\u2013", "-").replace("\u2014", "-")
#     value = re.sub(r"\s*-\s*", "-", value)
#     value = re.sub(r"\s+", "", value)
#     return value or None


# def coerce(field: str, value: Optional[str]):
#     if field == "zip_code":
#         return clean_zip(value)
#     value = clean(value)
#     if value is None:
#         return None
#     if field in INT_FIELDS:
#         try:
#             return int(float(value))
#         except ValueError:
#             return None
#     if field in FLOAT_FIELDS:
#         try:
#             return float(value)
#         except ValueError:
#             return None
#     return value


# def row_has_any(row: dict, columns: list[str]) -> bool:
#     return any(row.get(c) is not None for c in columns)


# def parse_extra_attributes(raw: Optional[str], source_uuid: Optional[str]) -> dict:
#     import json
#     extra = {}
#     raw = clean(raw)
#     if raw:
#         try:
#             parsed = json.loads(raw)
#             if isinstance(parsed, dict):
#                 extra.update(parsed)
#         except (json.JSONDecodeError, TypeError):
#             extra["_unparsed_extra_attributes"] = raw
#     if source_uuid:
#         # Original CSV's own uuid is NOT reused as our primary key (a future
#         # re-export of the same facility would get a fresh random uuid from
#         # the source, breaking our identity matching) -- we keep it here
#         # purely for traceability/debugging.
#         extra["source_uuid"] = source_uuid
#     return extra


# def dedupe_within_batch(rows: list[dict], report: dict) -> list[dict]:
#     """
#     Postgres cannot insert two rows with the same unique-indexed value within
#     a single statement. This is a safety net for rows in the SAME batch that
#     resolve to the same identity -- either because they're literal duplicate
#     lines, OR because the same brand-new facility appears twice under
#     different sources within one batch (e.g. a CMS row with ccn and a state-
#     directory row without ccn, sharing the same dedup_hash, neither of which
#     exists in the DB yet). We merge by dedup_hash FIRST (preferring whichever
#     row in the group carries a ccn, so we don't discard that identifier),
#     then do a final pass on ccn in case two different dedup_hash groups
#     still ended up sharing a ccn (defensive; not expected in practice).
#     """
#     original_count = len(rows)

#     by_hash: dict[str, dict] = {}
#     for row in rows:
#         existing = by_hash.get(row["dedup_hash"])
#         if existing is None or (existing["ccn"] is None and row["ccn"] is not None):
#             by_hash[row["dedup_hash"]] = row
#     merged = list(by_hash.values())

#     by_ccn: dict[str, dict] = {}
#     no_ccn: list[dict] = []
#     for row in merged:
#         if row["ccn"] is not None:
#             by_ccn[row["ccn"]] = row
#         else:
#             no_ccn.append(row)

#     deduped = list(by_ccn.values()) + no_ccn
#     skipped = original_count - len(deduped)
#     if skipped > 0:
#         report["skipped_in_batch_collisions"] = report.get("skipped_in_batch_collisions", 0) + skipped
#     return deduped


# def upsert_batch(cur, rows: list[dict], core_cols_with_extra: list[str], report: dict) -> list[tuple]:
#     """
#     Upserts one batch into `facilities` using a staging-table
#     UPDATE-then-INSERT pattern, matching each incoming row against existing
#     facilities by (in priority order) `source_uuid`, then `ccn`, then
#     `dedup_hash`.

#     `source_uuid` (the source file's own row id) is checked FIRST because
#     it's confirmed stable across re-exports of this data pipeline, unlike
#     `dedup_hash` -- which is derived from name+address+zip+state+type, so it
#     CHANGES if a later file cleans up/standardizes any of those fields (e.g.
#     a facility_type text correction). Without source_uuid-first matching, a
#     file that only fixes facility_type text would look like brand-new
#     facilities to the dedup_hash check and get inserted as duplicates
#     instead of updating the existing rows.

#     Why not a plain `INSERT ... ON CONFLICT`: real data contains the SAME
#     physical facility listed twice under different sources -- e.g. a state
#     directory record (no ccn) and a CMS record (has ccn) that share the same
#     normalized name+address+zip+state+type (and therefore the same
#     dedup_hash). `ON CONFLICT` can only target ONE unique index per
#     statement, so a ccn-keyed insert can still collide with an existing
#     dedup_hash-keyed row from a different source -- that's a genuine same-
#     facility match, not an error, and must UPDATE the existing row (also
#     backfilling its ccn) rather than fail.

#     Returns [(facility_id, row_dict), ...] for the caller to then upsert
#     into the relevant detail tables.
#     """
#     rows = dedupe_within_batch(rows, report)
#     if not rows:
#         return []

#     core_cols = ["id", "dedup_hash"] + core_cols_with_extra
#     indexed_rows = list(enumerate(rows))  # (row_seq, row_dict)

#     staging_cols = ["row_seq"] + core_cols
#     col_type_map = {
#         "row_seq": "INTEGER", "id": "UUID", "dedup_hash": "TEXT", "ccn": "TEXT",
#         "source_uuid": "TEXT",
#         "latitude": "DOUBLE PRECISION", "longitude": "DOUBLE PRECISION",
#         "bed_count": "INTEGER", "secure_memory_care_beds": "INTEGER", "cms_region": "INTEGER",
#         "overall_rating": "DOUBLE PRECISION",
#         "extra_attributes": "JSONB", "is_active": "BOOLEAN",
#     }
#     staging_col_defs = ", ".join(
#         f"{c} {col_type_map.get(c, 'TEXT')}" for c in staging_cols
#     )

#     cur.execute(f"CREATE TEMP TABLE batch_staging ({staging_col_defs}) ON COMMIT DROP")

#     staging_values = [
#         tuple([seq] + [row[c] for c in core_cols]) for seq, row in indexed_rows
#     ]
#     execute_values(
#         cur,
#         f"INSERT INTO batch_staging ({', '.join(staging_cols)}) VALUES %s",
#         staging_values,
#     )

#     # ---- Step 1: UPDATE existing facilities, matched by source_uuid first,
#     # falling back to ccn, then dedup_hash ----
#     update_cols = [c for c in core_cols if c not in ("id", "dedup_hash", "ccn", "is_active")]
#     set_clause = ", ".join(f"{c} = s.{c}" for c in update_cols)
#     cur.execute(
#         f"""
#         UPDATE facilities f
#         SET {set_clause},
#             ccn = COALESCE(f.ccn, s.ccn),
#             updated_at = now()
#         FROM batch_staging s
#         WHERE (s.source_uuid IS NOT NULL AND f.source_uuid = s.source_uuid)
#            OR (s.ccn IS NOT NULL AND f.ccn = s.ccn)
#            OR f.dedup_hash = s.dedup_hash
#         RETURNING f.id, s.row_seq
#         """
#     )
#     updated_map: dict[int, str] = {row_seq: str(fac_id) for fac_id, row_seq in cur.fetchall()}
#     report["updated"] += len(updated_map)

#     # ---- Step 2: INSERT the rest (guaranteed new -- didn't match anything above) ----
#     remaining_seqs = [seq for seq, _ in indexed_rows if seq not in updated_map]
#     if remaining_seqs:
#         insert_cols = ["id", "ccn", "dedup_hash"] + [
#             c for c in core_cols if c not in ("id", "dedup_hash", "ccn")
#         ]
#         cur.execute(
#             f"""
#             INSERT INTO facilities ({", ".join(insert_cols)})
#             SELECT {", ".join(f"s.{c}" for c in insert_cols)}
#             FROM batch_staging s
#             WHERE s.row_seq = ANY(%s)
#             """,
#             (remaining_seqs,),
#         )
#         report["inserted"] += len(remaining_seqs)

#     results: list[tuple] = []
#     for seq, row in indexed_rows:
#         facility_id = updated_map.get(seq, row["id"])
#         results.append((facility_id, row))
#     return results


# def upsert_detail_table(cur, table: str, columns: list[str], facility_rows: list[tuple]):
#     rows_to_write = [
#         (fac_id, *[r[c] for c in columns])
#         for fac_id, r in facility_rows
#         if row_has_any(r, columns)
#     ]
#     if not rows_to_write:
#         return
#     all_cols = ["facility_id"] + columns
#     set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
#     sql = f"""
#         INSERT INTO {table} ({", ".join(all_cols)})
#         VALUES %s
#         ON CONFLICT (facility_id) DO UPDATE SET {set_clause}
#     """
#     execute_values(cur, sql, rows_to_write)


# def process_batch(cur, raw_rows: list[dict], report: dict):
#     prepared = []
#     for raw in raw_rows:
#         name = clean(raw.get("name"))
#         if name is None:
#             # A small number of source rows (observed: 2 out of 60,037) are
#             # effectively blank -- only metadata columns populated, no name
#             # at all. There's nothing usable to identify or display such a
#             # "facility", so we skip it rather than let a NOT NULL violation
#             # abort the entire batch.
#             report["rejected_invalid"] = report.get("rejected_invalid", 0) + 1
#             continue

#         core = {c: coerce(c, raw.get(c)) for c in CORE_COLUMNS}
#         core["ccn"] = clean(raw.get("ccn"))
#         core["id"] = str(uuid.uuid4())
#         core["source_uuid"] = clean(raw.get("uuid"))
#         core["dedup_hash"] = compute_dedup_hash(
#             name=raw.get("name"), address=raw.get("address"),
#             zip_code=core["zip_code"], state=raw.get("state"),
#             facility_type=raw.get("facility_type"),
#         )
#         core["extra_attributes"] = Json(
#             parse_extra_attributes(raw.get("extra_attributes"), raw.get("uuid"))
#         )
#         core["is_active"] = True
#         nh = {c: coerce(c, raw.get(c)) for c in NH_COLUMNS}
#         hh = {c: coerce(c, raw.get(c)) for c in HH_COLUMNS}
#         services = {c: coerce(c, raw.get(c)) for c in SERVICE_COLUMNS}

#         prepared.append({**core, "nh": nh, "hh": hh, "services": services})

#     core_cols_with_extra = CORE_COLUMNS + ["source_uuid", "extra_attributes", "is_active"]
#     facility_results = upsert_batch(cur, prepared, core_cols_with_extra, report)

#     upsert_detail_table(cur, "nursing_home_details", NH_COLUMNS,
#                          [(fid, r["nh"]) for fid, r in facility_results])
#     upsert_detail_table(cur, "home_health_details", HH_COLUMNS,
#                          [(fid, r["hh"]) for fid, r in facility_results])
#     upsert_detail_table(cur, "facility_services", SERVICE_COLUMNS,
#                          [(fid, r["services"]) for fid, r in facility_results])


# def main():
#     parser = argparse.ArgumentParser(description="Import/upsert facility CSV data")
#     parser.add_argument("csv_path", type=str, help="Path to the facilities CSV file")
#     parser.add_argument("--dry-run", action="store_true", help="Parse and validate only, no DB writes")
#     args = parser.parse_args()

#     csv_path = Path(args.csv_path)
#     if not csv_path.exists():
#         print(f"ERROR: file not found: {csv_path}", file=sys.stderr)
#         sys.exit(1)

#     report = {"inserted": 0, "updated": 0, "total_rows": 0}
#     started = datetime.now(timezone.utc)

#     conn = None
#     if not args.dry_run:
#         conn = psycopg2.connect(settings.MIGRATION_DATABASE_URL)

#     try:
#         with open(csv_path, newline="", encoding="utf-8") as f:
#             reader = csv.DictReader(f)
#             batch = []
#             for row in reader:
#                 batch.append(row)
#                 report["total_rows"] += 1
#                 if len(batch) >= BATCH_SIZE:
#                     if not args.dry_run:
#                         with conn:
#                             with conn.cursor() as cur:
#                                 process_batch(cur, batch, report)
#                     batch = []
#             if batch and not args.dry_run:
#                 with conn:
#                     with conn.cursor() as cur:
#                         process_batch(cur, batch, report)
#     finally:
#         if conn is not None:
#             conn.close()

#     elapsed = (datetime.now(timezone.utc) - started).total_seconds()
#     print("=" * 50)
#     print(f"Import {'(DRY RUN) ' if args.dry_run else ''}complete in {elapsed:.1f}s")
#     print(f"  Total rows read      : {report['total_rows']}")
#     print(f"  Inserted (new)       : {report['inserted']}")
#     print(f"  Updated (matched)    : {report['updated']}")
#     print(f"  Skipped (in-file dup): {report.get('skipped_in_batch_collisions', 0)}")
#     print(f"  Rejected (invalid)   : {report.get('rejected_invalid', 0)}")
#     print("=" * 50)


# if __name__ == "__main__":
#     main()

































"""
Idempotent facility data importer -- curated 8-category schema.

Usage:
    python3 -m scripts.import_facilities /path/to/data_with_uuid.csv
    python3 -m scripts.import_facilities /path/to/data.csv --dry-run

Design goals:
  1. Re-running on the SAME file must NOT create duplicate rows.
  2. A NEW file overlapping existing facilities UPDATES those rows, not
     duplicates them.
  3. Matching priority: source_uuid -> ccn -> dedup_hash
     (see app/services/dedup.py -- the shared source of truth for the hash).
  4. Safe to abort and re-run: each batch is one transaction.
  5. Only the 8 approved facility categories are loaded. Any row whose
     facility_type_category is missing or outside the approved set is REJECTED
     before insert -- this keeps the DB CHECK constraint
     (ck_facilities_facility_type_category, migration c7f2a9d4e8b1) from
     aborting a whole batch, and reports the offending values so a category
     string mismatch between the CSV and the approved list is obvious.

Synchronous psycopg2 on purpose -- this is an offline ops script, and
execute_values gives fast batch upserts with RETURNING for accurate counts.
"""
import argparse
import csv
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.dedup import compute_dedup_hash  # noqa: E402

BATCH_SIZE = 1000

# KEEP IN SYNC with alembic migration c7f2a9d4e8b1's ALLOWED_FACILITY_CATEGORIES
# and the ck_facilities_facility_type_category CHECK constraint. A row whose
# facility_type_category is not EXACTLY one of these (after trim) is rejected.
# If the CSV uses different wording, map it in normalize_category() below --
# do NOT loosen the DB constraint.
ALLOWED_FACILITY_CATEGORIES = {
    "Adult Day Care",
    "Hospice",
    "Intermediate Care Facility (ICF/IID)",
    "Mental/Behavioral Health Facility",
    "Nursing Home / Skilled Nursing Facility",
    "Rehabilitation - Inpatient",
    "Rehabilitation - Outpatient",
    "Residential Care / Assisted Living",
}

# ---- Curated column sets (facilities + its 3 detail tables). ----
# Only display/data columns live here. id / dedup_hash / ccn / source_uuid /
# is_active are system columns handled explicitly in process_batch().
# created_at / updated_at are DB-managed (server_default now()), never written
# by the importer.
CORE_COLUMNS = [
    "ccn", "name", "facility_type", "facility_type_category", "ownership_type",
    "address", "city", "state", "zip_code", "county", "phone", "email",
    "facility_subtype", "secure_memory_care_beds", "npi_type",
    "latitude", "longitude", "bed_count", "overall_rating", "load_timestamp",
]

NH_COLUMNS = [
    "nh_special_focus_facility",
    "nh_health_inspection_star_rating",
    "nh_total_nursing_hours_per_resident_day",
    "nh_total_nursing_staff_turnover_pct",
]

HH_COLUMNS = [
    "hh_home_discharge_success",
    "hh_functional_ability_discharge_score",
    "hh_falls_major_injury_pct",
    "hh_developed_bedsores_pct",
    "hh_hospital_readmission_rate",
    "hh_started_care_on_time_pct",
]

SERVICE_COLUMNS = [
    "offers_alzheimer_dementia_care",
    "offers_hospice_care",
    "offers_ventilator_care",
    "offers_psychiatric_care",
    "offers_rehab_services",
    "offers_adult_day_care",
    "offers_respite_care",
    "offers_home_care_services",
    "offers_traumatic_brain_injury_care",
]

INT_FIELDS = {"bed_count", "secure_memory_care_beds"}
FLOAT_FIELDS = {
    "latitude", "longitude", "overall_rating",
    "nh_health_inspection_star_rating",
    "nh_total_nursing_hours_per_resident_day",
    "nh_total_nursing_staff_turnover_pct",
    "hh_home_discharge_success", "hh_functional_ability_discharge_score",
    "hh_falls_major_injury_pct", "hh_developed_bedsores_pct",
    "hh_hospital_readmission_rate", "hh_started_care_on_time_pct",
}


def clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def clean_zip(value: Optional[str]) -> Optional[str]:
    """Normalize inconsistent ZIP formatting ('20774 \u2013 1474' -> '20774-1474')."""
    value = clean(value)
    if value is None:
        return None
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = re.sub(r"\s*-\s*", "-", value)
    value = re.sub(r"\s+", "", value)
    return value or None


def coerce(field: str, value: Optional[str]):
    if field == "zip_code":
        return clean_zip(value)
    value = clean(value)
    if value is None:
        return None
    if field in INT_FIELDS:
        try:
            return int(float(value))
        except ValueError:
            return None
    if field in FLOAT_FIELDS:
        try:
            return float(value)
        except ValueError:
            return None
    return value


def normalize_category(value: Optional[str]) -> Optional[str]:
    """
    Map a raw CSV facility_type_category onto one of the 8 approved values.

    Right now this only trims whitespace and requires an exact match. If the
    incoming file labels categories differently (e.g. 'Assisted Living' instead
    of 'Residential Care / Assisted Living'), add the mapping here -- this is
    the ONE place to reconcile source wording with the approved set, so the DB
    CHECK constraint stays strict.
    """
    value = clean(value)
    if value is None:
        return None
    # Example of how to add a synonym mapping when the source wording differs:
    # SYNONYMS = {"Assisted Living": "Residential Care / Assisted Living"}
    # value = SYNONYMS.get(value, value)
    return value if value in ALLOWED_FACILITY_CATEGORIES else None


def row_has_any(row: dict, columns: list) -> bool:
    return any(row.get(c) is not None for c in columns)


def dedupe_within_batch(rows: list, report: dict) -> list:
    """
    Merge rows in the SAME batch that resolve to the same identity (duplicate
    lines, or the same new facility under two sources). Merge by dedup_hash
    first (preferring the row that carries a ccn), then a defensive pass on ccn.
    """
    original_count = len(rows)

    by_hash = {}
    for row in rows:
        existing = by_hash.get(row["dedup_hash"])
        if existing is None or (existing["ccn"] is None and row["ccn"] is not None):
            by_hash[row["dedup_hash"]] = row
    merged = list(by_hash.values())

    by_ccn = {}
    no_ccn = []
    for row in merged:
        if row["ccn"] is not None:
            by_ccn[row["ccn"]] = row
        else:
            no_ccn.append(row)

    deduped = list(by_ccn.values()) + no_ccn
    skipped = original_count - len(deduped)
    if skipped > 0:
        report["skipped_in_batch_collisions"] = report.get("skipped_in_batch_collisions", 0) + skipped
    return deduped


def upsert_batch(cur, rows: list, core_cols_with_system: list, report: dict) -> list:
    """
    Upsert one batch into `facilities` via a staging table, matching existing
    rows by source_uuid -> ccn -> dedup_hash (in that priority). Returns
    [(facility_id, row_dict), ...] for the detail-table upserts.
    """
    rows = dedupe_within_batch(rows, report)
    if not rows:
        return []

    core_cols = ["id", "dedup_hash"] + core_cols_with_system
    indexed_rows = list(enumerate(rows))

    staging_cols = ["row_seq"] + core_cols
    col_type_map = {
        "row_seq": "INTEGER", "id": "UUID", "dedup_hash": "TEXT", "ccn": "TEXT",
        "source_uuid": "TEXT",
        "latitude": "DOUBLE PRECISION", "longitude": "DOUBLE PRECISION",
        "bed_count": "INTEGER", "secure_memory_care_beds": "INTEGER",
        "overall_rating": "DOUBLE PRECISION",
        "is_active": "BOOLEAN",
    }
    staging_col_defs = ", ".join(f"{c} {col_type_map.get(c, 'TEXT')}" for c in staging_cols)
    cur.execute(f"CREATE TEMP TABLE batch_staging ({staging_col_defs}) ON COMMIT DROP")

    staging_values = [
        tuple([seq] + [row[c] for c in core_cols]) for seq, row in indexed_rows
    ]
    execute_values(
        cur,
        f"INSERT INTO batch_staging ({', '.join(staging_cols)}) VALUES %s",
        staging_values,
    )

    # Step 1: UPDATE existing, matched by source_uuid -> ccn -> dedup_hash.
    update_cols = [c for c in core_cols if c not in ("id", "dedup_hash", "ccn", "is_active")]
    set_clause = ", ".join(f"{c} = s.{c}" for c in update_cols)
    cur.execute(
        f"""
        UPDATE facilities f
        SET {set_clause},
            ccn = COALESCE(f.ccn, s.ccn),
            updated_at = now()
        FROM batch_staging s
        WHERE (s.source_uuid IS NOT NULL AND f.source_uuid = s.source_uuid)
           OR (s.ccn IS NOT NULL AND f.ccn = s.ccn)
           OR f.dedup_hash = s.dedup_hash
        RETURNING f.id, s.row_seq
        """
    )
    updated_map = {row_seq: str(fac_id) for fac_id, row_seq in cur.fetchall()}
    report["updated"] += len(updated_map)

    # Step 2: INSERT the rest (guaranteed new).
    remaining_seqs = [seq for seq, _ in indexed_rows if seq not in updated_map]
    if remaining_seqs:
        insert_cols = ["id", "ccn", "dedup_hash"] + [
            c for c in core_cols if c not in ("id", "dedup_hash", "ccn")
        ]
        cur.execute(
            f"""
            INSERT INTO facilities ({", ".join(insert_cols)})
            SELECT {", ".join(f"s.{c}" for c in insert_cols)}
            FROM batch_staging s
            WHERE s.row_seq = ANY(%s)
            """,
            (remaining_seqs,),
        )
        report["inserted"] += len(remaining_seqs)

    results = []
    for seq, row in indexed_rows:
        facility_id = updated_map.get(seq, row["id"])
        results.append((facility_id, row))
    return results


def upsert_detail_table(cur, table: str, columns: list, facility_rows: list):
    rows_to_write = [
        (fac_id, *[r[c] for c in columns])
        for fac_id, r in facility_rows
        if row_has_any(r, columns)
    ]
    if not rows_to_write:
        return
    all_cols = ["facility_id"] + columns
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
    sql = f"""
        INSERT INTO {table} ({", ".join(all_cols)})
        VALUES %s
        ON CONFLICT (facility_id) DO UPDATE SET {set_clause}
    """
    execute_values(cur, sql, rows_to_write)


def process_batch(cur, raw_rows: list, report: dict):
    prepared = []
    for raw in raw_rows:
        name = clean(raw.get("name"))
        if name is None:
            # Blank source rows (metadata only, no name) -- nothing to display,
            # skip rather than trip the NOT NULL and abort the batch.
            report["rejected_invalid"] = report.get("rejected_invalid", 0) + 1
            continue

        category = normalize_category(raw.get("facility_type_category"))
        if category is None:
            # Outside the 8 approved categories (or missing). Rejected before
            # insert so the CHECK constraint never aborts a transaction. Record
            # the raw value so a source/approved-list mismatch is diagnosable.
            report["rejected_wrong_type"] = report.get("rejected_wrong_type", 0) + 1
            raw_cat = clean(raw.get("facility_type_category")) or "<empty>"
            report["rejected_type_samples"][raw_cat] += 1
            continue

        core = {c: coerce(c, raw.get(c)) for c in CORE_COLUMNS}
        core["facility_type_category"] = category  # normalized, guaranteed valid
        core["ccn"] = clean(raw.get("ccn"))
        core["id"] = str(uuid.uuid4())
        core["source_uuid"] = clean(raw.get("uuid"))
        core["dedup_hash"] = compute_dedup_hash(
            name=raw.get("name"), address=raw.get("address"),
            zip_code=core["zip_code"], state=raw.get("state"),
            facility_type=raw.get("facility_type"),
        )
        core["is_active"] = True
        nh = {c: coerce(c, raw.get(c)) for c in NH_COLUMNS}
        hh = {c: coerce(c, raw.get(c)) for c in HH_COLUMNS}
        services = {c: coerce(c, raw.get(c)) for c in SERVICE_COLUMNS}

        prepared.append({**core, "nh": nh, "hh": hh, "services": services})

    core_cols_with_system = CORE_COLUMNS + ["source_uuid", "is_active"]
    facility_results = upsert_batch(cur, prepared, core_cols_with_system, report)

    upsert_detail_table(cur, "nursing_home_details", NH_COLUMNS,
                         [(fid, r["nh"]) for fid, r in facility_results])
    upsert_detail_table(cur, "home_health_details", HH_COLUMNS,
                         [(fid, r["hh"]) for fid, r in facility_results])
    upsert_detail_table(cur, "facility_services", SERVICE_COLUMNS,
                         [(fid, r["services"]) for fid, r in facility_results])


def main():
    parser = argparse.ArgumentParser(description="Import/upsert curated facility CSV data")
    parser.add_argument("csv_path", type=str, help="Path to the facilities CSV file")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate only, no DB writes")
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERROR: file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    report = {
        "inserted": 0, "updated": 0, "total_rows": 0,
        "rejected_type_samples": Counter(),
    }
    started = datetime.now(timezone.utc)

    conn = None
    if not args.dry_run:
        conn = psycopg2.connect(settings.MIGRATION_DATABASE_URL)

    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                batch.append(row)
                report["total_rows"] += 1
                if len(batch) >= BATCH_SIZE:
                    if not args.dry_run:
                        with conn:
                            with conn.cursor() as cur:
                                process_batch(cur, batch, report)
                    else:
                        _dry_run_validate(batch, report)
                    batch = []
            if batch:
                if not args.dry_run:
                    with conn:
                        with conn.cursor() as cur:
                            process_batch(cur, batch, report)
                else:
                    _dry_run_validate(batch, report)
    finally:
        if conn is not None:
            conn.close()

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print("=" * 60)
    print(f"Import {'(DRY RUN) ' if args.dry_run else ''}complete in {elapsed:.1f}s")
    print(f"  Total rows read        : {report['total_rows']}")
    print(f"  Inserted (new)         : {report['inserted']}")
    print(f"  Updated (matched)      : {report['updated']}")
    print(f"  Skipped (in-file dup)  : {report.get('skipped_in_batch_collisions', 0)}")
    print(f"  Rejected (no name)     : {report.get('rejected_invalid', 0)}")
    print(f"  Rejected (wrong type)  : {report.get('rejected_wrong_type', 0)}")
    samples = report["rejected_type_samples"]
    if samples:
        print("  -- top rejected facility_type_category values (fix wording in "
              "normalize_category if these should have been kept):")
        for value, count in samples.most_common(20):
            print(f"       {count:>8,}  {value!r}")
    print("=" * 60)


def _dry_run_validate(raw_rows: list, report: dict):
    """Count what WOULD happen without touching the DB (name + category checks)."""
    for raw in raw_rows:
        if clean(raw.get("name")) is None:
            report["rejected_invalid"] = report.get("rejected_invalid", 0) + 1
            continue
        if normalize_category(raw.get("facility_type_category")) is None:
            report["rejected_wrong_type"] = report.get("rejected_wrong_type", 0) + 1
            raw_cat = clean(raw.get("facility_type_category")) or "<empty>"
            report["rejected_type_samples"][raw_cat] += 1


if __name__ == "__main__":
    main()