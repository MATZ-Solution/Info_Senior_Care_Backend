# """
# Facility models -- normalized to avoid one giant sparse 95-column table.

#   facilities            -> core fields every screen/list needs (~20 cols)
#   nursing_home_details   -> nh_* fields, 1:1, only populated for Nursing Homes
#   home_health_details    -> hh_* fields, 1:1, only populated for Home Health
#   facility_services      -> offers_* boolean flags, 1:1

# Why split: `facilities/search` (highest-traffic endpoint, hit by every user
# on Home/Search screens) only ever touches the core table. The detail screen
# is the only place that pays the cost of joining a wide detail table, and
# even then only ONE of the two detail tables (based on facility_type).
# """
# import uuid as uuid_lib

# from sqlalchemy import (
#     Boolean,
#     DateTime,
#     Float,
#     ForeignKey,
#     Index,
#     Integer,
#     String,
#     Text,
#     func,
# )
# from sqlalchemy.dialects.postgresql import JSONB, UUID
# from sqlalchemy.orm import Mapped, mapped_column, relationship

# from app.core.database import Base


# class Facility(Base):
#     __tablename__ = "facilities"

#     id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
#     )

#     # External identity (CMS Certification Number) -- present for ~19% of
#     # records (CMS-sourced rows). Nullable; uniqueness enforced by a partial
#     # index (see migration) so multiple NULLs are allowed.
#     ccn: Mapped[str | None] = mapped_column(String(20), nullable=True)

#     # Our own dedup key, ALWAYS computed (see services/dedup.py), used as
#     # the fallback match key when ccn is absent. This is what makes future
#     # file imports idempotent.
#     dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)

#     # The SOURCE file's own row identifier (its "uuid" column). Confirmed
#     # stable across re-exports of this specific data pipeline (unlike a
#     # typical external uuid, which usually regenerates per export) -- so
#     # this is actually the MOST reliable match key across re-imports,
#     # taking priority over ccn/dedup_hash in scripts/import_facilities.py.
#     # It is intentionally separate from our own `id` primary key (which
#     # stays ours to fully control) so a source-side re-export can never
#     # collide with or dictate our internal primary key values.
#     source_uuid: Mapped[str | None] = mapped_column(String(64), index=True)

#     name: Mapped[str] = mapped_column(String(500), nullable=False)
#     facility_type: Mapped[str | None] = mapped_column(String(200), index=True)
#     # Standardized/cleaned bucket (small fixed set, e.g. "Nursing Home /
#     # Skilled Nursing Facility", "Home Health Agency", "Hospice") -- this is
#     # what the frontend's hardcoded filter dropdown sends, as opposed to
#     # `facility_type` which is raw/messy source text with many variants.
#     facility_type_category: Mapped[str | None] = mapped_column(String(100), index=True)
#     legal_business_name: Mapped[str | None] = mapped_column(String(500))
#     ownership_type: Mapped[str | None] = mapped_column(String(200))

#     address: Mapped[str | None] = mapped_column(String(500))
#     city: Mapped[str | None] = mapped_column(String(200), index=True)
#     state: Mapped[str | None] = mapped_column(String(2), index=True)
#     zip_code: Mapped[str | None] = mapped_column(String(20), index=True)
#     county: Mapped[str | None] = mapped_column(String(200))


#     phone: Mapped[str | None] = mapped_column(String(30))
#     email: Mapped[str | None] = mapped_column(String(300))

#     facility_subtype: Mapped[str | None] = mapped_column(String(200))
#     operating_status: Mapped[str | None] = mapped_column(String(50))
#     closed_date: Mapped[str | None] = mapped_column(String(30))

#     latitude: Mapped[float | None] = mapped_column(Float)
#     longitude: Mapped[float | None] = mapped_column(Float)

#     bed_count: Mapped[int | None] = mapped_column(Integer)
#     overall_rating: Mapped[float | None] = mapped_column(Float)

#     data_source: Mapped[str | None] = mapped_column(String(300))
#     source_file: Mapped[str | None] = mapped_column(String(300))
#     schema_version: Mapped[str | None] = mapped_column(String(20))
#     load_timestamp: Mapped[str | None] = mapped_column(String(50))

#     # Catch-all for source-specific fields that don't warrant their own
#     # column (e.g. {"CONTACT_INFO": "..."}) -- avoids schema churn every
#     # time a new source file has one weird extra field.
#     extra_attributes: Mapped[dict | None] = mapped_column(JSONB)

#     is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

#     created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
#     updated_at: Mapped[object] = mapped_column(
#         DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
#     )

#     nursing_home_detail: Mapped["NursingHomeDetails | None"] = relationship(
#         back_populates="facility", uselist=False, cascade="all, delete-orphan"
#     )
#     home_health_detail: Mapped["HomeHealthDetails | None"] = relationship(
#         back_populates="facility", uselist=False, cascade="all, delete-orphan"
#     )
#     services: Mapped["FacilityServices | None"] = relationship(
#         back_populates="facility", uselist=False, cascade="all, delete-orphan"
#     )

#     # NOTE: the partial unique index on `ccn` (WHERE ccn IS NOT NULL) and the
#     # unique index on `dedup_hash` are created explicitly in the Alembic
#     # migration (raw SQL) rather than here, since SQLAlchemy's declarative
#     # partial-index syntax is awkward to express cleanly at class-body scope.
#     __table_args__ = (
#         Index("ix_facilities_state_type", "state", "facility_type"),
#     )


# class NursingHomeDetails(Base):
#     __tablename__ = "nursing_home_details"

#     facility_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), primary_key=True
#     )

#     nh_total_certified_beds: Mapped[int | None] = mapped_column(Integer)
#     nh_average_daily_residents: Mapped[float | None] = mapped_column(Float)
#     nh_chain_affiliation: Mapped[str | None] = mapped_column(String(300))
#     nh_ccrc: Mapped[str | None] = mapped_column(String(50))
#     nh_abuse_complaint: Mapped[str | None] = mapped_column(String(50))
#     nh_special_focus_facility: Mapped[str | None] = mapped_column(String(50))
#     nh_sprinkler_system: Mapped[str | None] = mapped_column(String(50))
#     nh_health_inspection_star_rating: Mapped[float | None] = mapped_column(Float)
#     nh_staffing_star_rating: Mapped[float | None] = mapped_column(Float)
#     nh_quality_measure_star_rating: Mapped[float | None] = mapped_column(Float)
#     nh_total_nursing_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
#     nh_rn_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
#     nh_lpn_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
#     nh_cna_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
#     nh_pt_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
#     nh_staffing_level_assessment: Mapped[str | None] = mapped_column(Text)
#     nh_total_nursing_staff_turnover_pct: Mapped[float | None] = mapped_column(Float)
#     nh_rn_turnover_pct: Mapped[float | None] = mapped_column(Float)
#     nh_administrators_left_12mo: Mapped[int | None] = mapped_column(Integer)
#     nh_staff_stability: Mapped[str | None] = mapped_column(String(300))
#     nh_health_deficiencies_latest: Mapped[int | None] = mapped_column(Integer)
#     nh_health_deficiency_severity_score: Mapped[float | None] = mapped_column(Float)
#     nh_weighted_health_inspection_score: Mapped[float | None] = mapped_column(Float)
#     nh_number_of_fines: Mapped[int | None] = mapped_column(Integer)
#     nh_total_fines_usd: Mapped[float | None] = mapped_column(Float)
#     nh_medicare_payment_denials: Mapped[int | None] = mapped_column(Integer)
#     nh_total_penalties: Mapped[int | None] = mapped_column(Integer)
#     nh_infection_control_citations: Mapped[int | None] = mapped_column(Integer)
#     nh_penalty_summary: Mapped[str | None] = mapped_column(Text)

#     facility: Mapped["Facility"] = relationship(back_populates="nursing_home_detail")


# class HomeHealthDetails(Base):
#     __tablename__ = "home_health_details"

#     facility_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), primary_key=True
#     )

#     hh_provides_nursing_care: Mapped[str | None] = mapped_column(String(10))
#     hh_provides_physical_therapy: Mapped[str | None] = mapped_column(String(10))
#     hh_provides_occupational_therapy: Mapped[str | None] = mapped_column(String(10))
#     hh_provides_speech_therapy: Mapped[str | None] = mapped_column(String(10))
#     hh_provides_medical_social_services: Mapped[str | None] = mapped_column(String(10))
#     hh_provides_home_health_aides: Mapped[str | None] = mapped_column(String(10))
#     hh_improved_walking_mobility_pct: Mapped[float | None] = mapped_column(Float)
#     hh_improved_getting_out_of_bed_pct: Mapped[float | None] = mapped_column(Float)
#     hh_improved_bathing_ability_pct: Mapped[float | None] = mapped_column(Float)
#     hh_improved_breathing_pct: Mapped[float | None] = mapped_column(Float)
#     hh_improved_taking_medications_pct: Mapped[float | None] = mapped_column(Float)
#     hh_developed_bedsores_pct: Mapped[float | None] = mapped_column(Float)
#     hh_falls_major_injury_pct: Mapped[float | None] = mapped_column(Float)
#     hh_started_care_on_time_pct: Mapped[float | None] = mapped_column(Float)
#     hh_medication_issues_fixed_on_time_pct: Mapped[float | None] = mapped_column(Float)
#     hh_functional_ability_discharge_score: Mapped[float | None] = mapped_column(Float)
#     hh_info_shared_with_doctor_pct: Mapped[float | None] = mapped_column(Float)
#     hh_info_shared_with_family_pct: Mapped[float | None] = mapped_column(Float)
#     hh_home_discharge_success: Mapped[float | None] = mapped_column(Float)
#     hh_hospital_readmission_rate: Mapped[float | None] = mapped_column(Float)
#     hh_avoidable_hospitalizations: Mapped[float | None] = mapped_column(Float)
#     hh_medicare_cost_vs_national_avg: Mapped[str | None] = mapped_column(String(100))

#     facility: Mapped["Facility"] = relationship(back_populates="home_health_detail")


# class FacilityServices(Base):
#     __tablename__ = "facility_services"

#     facility_id: Mapped[uuid_lib.UUID] = mapped_column(
#         UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), primary_key=True
#     )

#     offers_alzheimer_dementia_care: Mapped[str | None] = mapped_column(String(10))
#     offers_hospice_care: Mapped[str | None] = mapped_column(String(10))
#     offers_ventilator_care: Mapped[str | None] = mapped_column(String(10))
#     offers_psychiatric_care: Mapped[str | None] = mapped_column(String(10))
#     offers_substance_abuse_treatment: Mapped[str | None] = mapped_column(String(10))
#     offers_hiv_care: Mapped[str | None] = mapped_column(String(10))
#     offers_rehab_services: Mapped[str | None] = mapped_column(String(10))
#     offers_adult_day_care: Mapped[str | None] = mapped_column(String(10))
#     offers_respite_care: Mapped[str | None] = mapped_column(String(10))
#     offers_home_care_services: Mapped[str | None] = mapped_column(String(10))
#     offers_traumatic_brain_injury_care: Mapped[str | None] = mapped_column(String(10))
#     offers_iv_therapy: Mapped[str | None] = mapped_column(String(10))
#     offers_pain_management: Mapped[str | None] = mapped_column(String(10))
#     offers_medical_equipment_supply: Mapped[str | None] = mapped_column(String(10))

#     facility: Mapped["Facility"] = relationship(back_populates="services")
































"""
Facility models -- normalized to avoid one giant sparse 95-column table.

  facilities            -> core fields every screen/list needs (~20 cols)
  nursing_home_details   -> nh_* fields, 1:1, only populated for Nursing Homes
  home_health_details    -> hh_* fields, 1:1, only populated for Home Health
  facility_services      -> offers_* boolean flags, 1:1

Why split: `facilities/search` (highest-traffic endpoint, hit by every user
on Home/Search screens) only ever touches the core table. The detail screen
is the only place that pays the cost of joining a wide detail table, and
even then only ONE of the two detail tables (based on facility_type).
"""
import uuid as uuid_lib

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Facility(Base):
    __tablename__ = "facilities"

    id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
    )

    # External identity (CMS Certification Number) -- present for ~19% of
    # records (CMS-sourced rows). Nullable; uniqueness enforced by a partial
    # index (see migration) so multiple NULLs are allowed.
    ccn: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Our own dedup key, ALWAYS computed (see services/dedup.py), used as
    # the fallback match key when ccn is absent. This is what makes future
    # file imports idempotent.
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # The SOURCE file's own row identifier (its "uuid" column). Confirmed
    # stable across re-exports of this specific data pipeline (unlike a
    # typical external uuid, which usually regenerates per export) -- so
    # this is actually the MOST reliable match key across re-imports,
    # taking priority over ccn/dedup_hash in scripts/import_facilities.py.
    # It is intentionally separate from our own `id` primary key (which
    # stays ours to fully control) so a source-side re-export can never
    # collide with or dictate our internal primary key values.
    source_uuid: Mapped[str | None] = mapped_column(String(64), index=True)

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    facility_type: Mapped[str | None] = mapped_column(String(200), index=True)
    # Standardized/cleaned bucket (small fixed set, e.g. "Nursing Home /
    # Skilled Nursing Facility", "Home Health Agency", "Hospice") -- this is
    # what the frontend's hardcoded filter dropdown sends, as opposed to
    # `facility_type` which is raw/messy source text with many variants.
    facility_type_category: Mapped[str | None] = mapped_column(String(100), index=True)
    legal_business_name: Mapped[str | None] = mapped_column(String(500))
    ownership_type: Mapped[str | None] = mapped_column(String(200))

    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(200), index=True)
    state: Mapped[str | None] = mapped_column(String(2), index=True)
    zip_code: Mapped[str | None] = mapped_column(String(20), index=True)
    county: Mapped[str | None] = mapped_column(String(200))


    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(300))

    facility_subtype: Mapped[str | None] = mapped_column(String(200))
    operating_status: Mapped[str | None] = mapped_column(String(50))
    closed_date: Mapped[str | None] = mapped_column(String(30))
    certification_date: Mapped[str | None] = mapped_column(String(30))

    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    bed_count: Mapped[int | None] = mapped_column(Integer)
    # Dementia/memory-care-specific secure bed capacity -- distinct from
    # the general bed_count; only populated for facilities that offer a
    # secure memory care unit.
    secure_memory_care_beds: Mapped[int | None] = mapped_column(Integer)
    overall_rating: Mapped[float | None] = mapped_column(Float)
    # CMS's own regional grouping number for the facility (source data
    # provides this as e.g. "4.0" -- coerced to a plain integer).
    cms_region: Mapped[int | None] = mapped_column(Integer)
    # Free-text notes from the source file about specialty services/focus
    # areas not otherwise captured in a structured column.
    specialty_notes: Mapped[str | None] = mapped_column(Text)
    # Which STATE'S source data file this row was extracted from -- this is
    # NOT always the same as the facility's actual physical `state` (e.g. a
    # multi-state provider's HQ record can appear in another state's
    # directory file). Kept separate from `state` deliberately: `state`
    # drives location search/filtering (the physical address), while this
    # is provenance/data-quality metadata only -- never use this for
    # location filtering.
    source_state_abbr: Mapped[str | None] = mapped_column(String(2))

    data_source: Mapped[str | None] = mapped_column(String(300))
    source_file: Mapped[str | None] = mapped_column(String(300))
    schema_version: Mapped[str | None] = mapped_column(String(20))
    load_timestamp: Mapped[str | None] = mapped_column(String(50))

    # Catch-all for source-specific fields that don't warrant their own
    # column (e.g. {"CONTACT_INFO": "..."}) -- avoids schema churn every
    # time a new source file has one weird extra field.
    extra_attributes: Mapped[dict | None] = mapped_column(JSONB)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    nursing_home_detail: Mapped["NursingHomeDetails | None"] = relationship(
        back_populates="facility", uselist=False, cascade="all, delete-orphan"
    )
    home_health_detail: Mapped["HomeHealthDetails | None"] = relationship(
        back_populates="facility", uselist=False, cascade="all, delete-orphan"
    )
    services: Mapped["FacilityServices | None"] = relationship(
        back_populates="facility", uselist=False, cascade="all, delete-orphan"
    )

    # NOTE: the partial unique index on `ccn` (WHERE ccn IS NOT NULL) and the
    # unique index on `dedup_hash` are created explicitly in the Alembic
    # migration (raw SQL) rather than here, since SQLAlchemy's declarative
    # partial-index syntax is awkward to express cleanly at class-body scope.
    __table_args__ = (
        Index("ix_facilities_state_type", "state", "facility_type"),
    )


class NursingHomeDetails(Base):
    __tablename__ = "nursing_home_details"

    facility_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), primary_key=True
    )

    nh_total_certified_beds: Mapped[int | None] = mapped_column(Integer)
    nh_average_daily_residents: Mapped[float | None] = mapped_column(Float)
    nh_chain_affiliation: Mapped[str | None] = mapped_column(String(300))
    nh_ccrc: Mapped[str | None] = mapped_column(String(50))
    nh_abuse_complaint: Mapped[str | None] = mapped_column(String(50))
    nh_special_focus_facility: Mapped[str | None] = mapped_column(String(50))
    nh_sprinkler_system: Mapped[str | None] = mapped_column(String(50))
    nh_health_inspection_star_rating: Mapped[float | None] = mapped_column(Float)
    nh_staffing_star_rating: Mapped[float | None] = mapped_column(Float)
    nh_quality_measure_star_rating: Mapped[float | None] = mapped_column(Float)
    nh_total_nursing_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
    nh_rn_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
    nh_lpn_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
    nh_cna_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
    nh_pt_hours_per_resident_day: Mapped[float | None] = mapped_column(Float)
    nh_staffing_level_assessment: Mapped[str | None] = mapped_column(Text)
    nh_total_nursing_staff_turnover_pct: Mapped[float | None] = mapped_column(Float)
    nh_rn_turnover_pct: Mapped[float | None] = mapped_column(Float)
    nh_administrators_left_12mo: Mapped[int | None] = mapped_column(Integer)
    nh_staff_stability: Mapped[str | None] = mapped_column(String(300))
    nh_health_deficiencies_latest: Mapped[int | None] = mapped_column(Integer)
    nh_health_deficiency_severity_score: Mapped[float | None] = mapped_column(Float)
    nh_weighted_health_inspection_score: Mapped[float | None] = mapped_column(Float)
    nh_number_of_fines: Mapped[int | None] = mapped_column(Integer)
    nh_total_fines_usd: Mapped[float | None] = mapped_column(Float)
    nh_medicare_payment_denials: Mapped[int | None] = mapped_column(Integer)
    nh_total_penalties: Mapped[int | None] = mapped_column(Integer)
    nh_infection_control_citations: Mapped[int | None] = mapped_column(Integer)
    nh_penalty_summary: Mapped[str | None] = mapped_column(Text)

    facility: Mapped["Facility"] = relationship(back_populates="nursing_home_detail")


class HomeHealthDetails(Base):
    __tablename__ = "home_health_details"

    facility_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), primary_key=True
    )

    hh_provides_nursing_care: Mapped[str | None] = mapped_column(String(10))
    hh_provides_physical_therapy: Mapped[str | None] = mapped_column(String(10))
    hh_provides_occupational_therapy: Mapped[str | None] = mapped_column(String(10))
    hh_provides_speech_therapy: Mapped[str | None] = mapped_column(String(10))
    hh_provides_medical_social_services: Mapped[str | None] = mapped_column(String(10))
    hh_provides_home_health_aides: Mapped[str | None] = mapped_column(String(10))
    hh_improved_walking_mobility_pct: Mapped[float | None] = mapped_column(Float)
    hh_improved_getting_out_of_bed_pct: Mapped[float | None] = mapped_column(Float)
    hh_improved_bathing_ability_pct: Mapped[float | None] = mapped_column(Float)
    hh_improved_breathing_pct: Mapped[float | None] = mapped_column(Float)
    hh_improved_taking_medications_pct: Mapped[float | None] = mapped_column(Float)
    hh_developed_bedsores_pct: Mapped[float | None] = mapped_column(Float)
    hh_falls_major_injury_pct: Mapped[float | None] = mapped_column(Float)
    hh_started_care_on_time_pct: Mapped[float | None] = mapped_column(Float)
    hh_medication_issues_fixed_on_time_pct: Mapped[float | None] = mapped_column(Float)
    hh_functional_ability_discharge_score: Mapped[float | None] = mapped_column(Float)
    hh_info_shared_with_doctor_pct: Mapped[float | None] = mapped_column(Float)
    hh_info_shared_with_family_pct: Mapped[float | None] = mapped_column(Float)
    hh_home_discharge_success: Mapped[float | None] = mapped_column(Float)
    hh_hospital_readmission_rate: Mapped[float | None] = mapped_column(Float)
    hh_avoidable_hospitalizations: Mapped[float | None] = mapped_column(Float)
    hh_medicare_cost_vs_national_avg: Mapped[str | None] = mapped_column(String(100))

    facility: Mapped["Facility"] = relationship(back_populates="home_health_detail")


class FacilityServices(Base):
    __tablename__ = "facility_services"

    facility_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id", ondelete="CASCADE"), primary_key=True
    )

    offers_alzheimer_dementia_care: Mapped[str | None] = mapped_column(String(10))
    offers_hospice_care: Mapped[str | None] = mapped_column(String(10))
    offers_ventilator_care: Mapped[str | None] = mapped_column(String(10))
    offers_psychiatric_care: Mapped[str | None] = mapped_column(String(10))
    offers_substance_abuse_treatment: Mapped[str | None] = mapped_column(String(10))
    offers_hiv_care: Mapped[str | None] = mapped_column(String(10))
    offers_rehab_services: Mapped[str | None] = mapped_column(String(10))
    offers_adult_day_care: Mapped[str | None] = mapped_column(String(10))
    offers_respite_care: Mapped[str | None] = mapped_column(String(10))
    offers_home_care_services: Mapped[str | None] = mapped_column(String(10))
    offers_traumatic_brain_injury_care: Mapped[str | None] = mapped_column(String(10))
    offers_iv_therapy: Mapped[str | None] = mapped_column(String(10))
    offers_pain_management: Mapped[str | None] = mapped_column(String(10))
    offers_medical_equipment_supply: Mapped[str | None] = mapped_column(String(10))

    facility: Mapped["Facility"] = relationship(back_populates="services")