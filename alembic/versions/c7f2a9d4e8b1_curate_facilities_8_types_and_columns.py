"""Curate facilities to 8 types + reduced columns, add npi_type

WHAT THIS DOES (per product decision -- fresh reload of a curated dataset):
  1. WIPES all facility data (facilities + its 3 detail tables) so the new
     300k-row file can be loaded clean. This is DESTRUCTIVE and irreversible
     for the *data* -- take a pg_dump backup first (see the runbook the team
     was given). The downgrade() below only restores the *schema* shape, not
     the rows.
  2. Trims the schema down to the columns the app actually shows, dropping the
     metadata/quality columns that are no longer surfaced.
  3. Adds the new `npi_type` column (was not present in any prior migration).
  4. Locks facility_type_category to the 8 approved categories via a CHECK
     constraint -- any row outside the 8 is rejected at the DB level.

The multi-table split (facilities + nursing_home_details + home_health_details
+ facility_services) is intentionally KEPT -- the read/search layer JOINs
those, so flattening would break it. Only the column *set* changes here.

Revision ID: c7f2a9d4e8b1
Revises: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c7f2a9d4e8b1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


# The 8 approved facility categories. KEEP IN SYNC with
# scripts/import_facilities.py::ALLOWED_FACILITY_CATEGORIES -- the importer
# filters to exactly this set so it never feeds the CHECK a value that would
# abort a whole batch.
ALLOWED_FACILITY_CATEGORIES = [
    "Adult Day Care",
    "Hospice",
    "Intermediate Care Facility (ICF/IID)",
    "Mental/Behavioral Health Facility",
    "Nursing Home / Skilled Nursing Facility",
    "Rehabilitation - Inpatient",
    "Rehabilitation - Outpatient",
    "Residential Care / Assisted Living",
]

CK_NAME = "ck_facilities_facility_type_category"

# ---- Columns being REMOVED, kept as (name, type) so downgrade can restore the
# schema shape (not the data). Grouped per table. ----

FACILITIES_DROP = [
    ("legal_business_name", sa.String(length=500)),
    ("operating_status", sa.String(length=50)),
    ("closed_date", sa.String(length=30)),
    ("certification_date", sa.String(length=30)),
    ("cms_region", sa.Integer()),
    ("specialty_notes", sa.Text()),
    ("source_state_abbr", sa.String(length=2)),
    ("data_source", sa.String(length=300)),
    ("source_file", sa.String(length=300)),
    ("schema_version", sa.String(length=20)),
    ("extra_attributes", postgresql.JSONB(astext_type=sa.Text())),
]

NH_DROP = [
    ("nh_total_certified_beds", sa.Integer()),
    ("nh_average_daily_residents", sa.Float()),
    ("nh_chain_affiliation", sa.String(length=300)),
    ("nh_ccrc", sa.String(length=50)),
    ("nh_abuse_complaint", sa.String(length=50)),
    ("nh_sprinkler_system", sa.String(length=50)),
    ("nh_staffing_star_rating", sa.Float()),
    ("nh_quality_measure_star_rating", sa.Float()),
    ("nh_rn_hours_per_resident_day", sa.Float()),
    ("nh_lpn_hours_per_resident_day", sa.Float()),
    ("nh_cna_hours_per_resident_day", sa.Float()),
    ("nh_pt_hours_per_resident_day", sa.Float()),
    ("nh_staffing_level_assessment", sa.Text()),
    ("nh_rn_turnover_pct", sa.Float()),
    ("nh_administrators_left_12mo", sa.Integer()),
    ("nh_staff_stability", sa.String(length=300)),
    ("nh_health_deficiencies_latest", sa.Integer()),
    ("nh_health_deficiency_severity_score", sa.Float()),
    ("nh_weighted_health_inspection_score", sa.Float()),
    ("nh_number_of_fines", sa.Integer()),
    ("nh_total_fines_usd", sa.Float()),
    ("nh_medicare_payment_denials", sa.Integer()),
    ("nh_total_penalties", sa.Integer()),
    ("nh_infection_control_citations", sa.Integer()),
    ("nh_penalty_summary", sa.Text()),
]

HH_DROP = [
    ("hh_provides_nursing_care", sa.String(length=10)),
    ("hh_provides_physical_therapy", sa.String(length=10)),
    ("hh_provides_occupational_therapy", sa.String(length=10)),
    ("hh_provides_speech_therapy", sa.String(length=10)),
    ("hh_provides_medical_social_services", sa.String(length=10)),
    ("hh_provides_home_health_aides", sa.String(length=10)),
    ("hh_improved_walking_mobility_pct", sa.Float()),
    ("hh_improved_getting_out_of_bed_pct", sa.Float()),
    ("hh_improved_bathing_ability_pct", sa.Float()),
    ("hh_improved_breathing_pct", sa.Float()),
    ("hh_improved_taking_medications_pct", sa.Float()),
    ("hh_medication_issues_fixed_on_time_pct", sa.Float()),
    ("hh_info_shared_with_doctor_pct", sa.Float()),
    ("hh_info_shared_with_family_pct", sa.Float()),
    ("hh_avoidable_hospitalizations", sa.Float()),
    ("hh_medicare_cost_vs_national_avg", sa.String(length=100)),
]

SERVICE_DROP = [
    ("offers_substance_abuse_treatment", sa.String(length=10)),
    ("offers_hiv_care", sa.String(length=10)),
    ("offers_iv_therapy", sa.String(length=10)),
    ("offers_pain_management", sa.String(length=10)),
    ("offers_medical_equipment_supply", sa.String(length=10)),
]


def upgrade() -> None:
    # 1. Wipe existing data. TRUNCATE ... CASCADE also empties the detail
    #    tables (they FK to facilities.id), so one statement clears all four.
    #    Done BEFORE adding the CHECK constraint -- otherwise pre-existing rows
    #    with non-approved categories would make the constraint creation fail.
    op.execute("TRUNCATE TABLE facilities RESTART IDENTITY CASCADE")

    # 2. New column.
    op.add_column("facilities", sa.Column("npi_type", sa.String(length=20), nullable=True))

    # 3. Drop the columns the app no longer surfaces.
    for name, _ in FACILITIES_DROP:
        op.drop_column("facilities", name)
    for name, _ in NH_DROP:
        op.drop_column("nursing_home_details", name)
    for name, _ in HH_DROP:
        op.drop_column("home_health_details", name)
    for name, _ in SERVICE_DROP:
        op.drop_column("facility_services", name)

    # 4. Lock facility_type_category to the 8 approved values.
    values = ", ".join(f"'{c}'" for c in ALLOWED_FACILITY_CATEGORIES)
    op.create_check_constraint(
        CK_NAME,
        "facilities",
        f"facility_type_category IN ({values})",
    )


def downgrade() -> None:
    # Restores the schema shape only. The truncated rows are NOT recoverable
    # from this migration -- restore them from the pre-migration pg_dump.
    op.drop_constraint(CK_NAME, "facilities", type_="check")

    for name, col_type in FACILITIES_DROP:
        op.add_column("facilities", sa.Column(name, col_type, nullable=True))
    for name, col_type in NH_DROP:
        op.add_column("nursing_home_details", sa.Column(name, col_type, nullable=True))
    for name, col_type in HH_DROP:
        op.add_column("home_health_details", sa.Column(name, col_type, nullable=True))
    for name, col_type in SERVICE_DROP:
        op.add_column("facility_services", sa.Column(name, col_type, nullable=True))

    op.drop_column("facilities", "npi_type")
