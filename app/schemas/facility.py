# """Facility response/request schemas."""
# from typing import Optional

# from app.schemas.common import UUIDStrMixin
# from pydantic import BaseModel, ConfigDict, Field


# class FacilityCard(UUIDStrMixin):
#     """Slim shape for list/search/map/recommended views -- core table only."""
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     name: str
#     facility_type: Optional[str] = None
#     facility_type_category: Optional[str] = None
#     city: Optional[str] = None
#     state: Optional[str] = None
#     zip_code: Optional[str] = None
#     latitude: Optional[float] = None
#     longitude: Optional[float] = None
#     overall_rating: Optional[float] = None
#     bed_count: Optional[int] = None
#     ownership_type: Optional[str] = None


# class FacilitySuggestItem(UUIDStrMixin):
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     name: str
#     address: Optional[str] = None
#     city: Optional[str] = None
#     state: Optional[str] = None
#     zip_code: Optional[str] = None


# class PaginatedFacilities(BaseModel):
#     items: list[FacilityCard]
#     page: int
#     page_size: int
#     total: int
#     has_more: bool


# class NursingHomeDetailOut(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     nh_total_certified_beds: Optional[int] = None
#     nh_average_daily_residents: Optional[float] = None
#     nh_chain_affiliation: Optional[str] = None
#     nh_ccrc: Optional[str] = None
#     nh_health_inspection_star_rating: Optional[float] = None
#     nh_staffing_star_rating: Optional[float] = None
#     nh_quality_measure_star_rating: Optional[float] = None
#     nh_total_nursing_hours_per_resident_day: Optional[float] = None
#     nh_staff_stability: Optional[str] = None
#     nh_health_deficiencies_latest: Optional[int] = None
#     nh_number_of_fines: Optional[int] = None
#     nh_total_fines_usd: Optional[float] = None
#     nh_penalty_summary: Optional[str] = None


# class HomeHealthDetailOut(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     hh_provides_nursing_care: Optional[str] = None
#     hh_provides_physical_therapy: Optional[str] = None
#     hh_provides_occupational_therapy: Optional[str] = None
#     hh_provides_speech_therapy: Optional[str] = None
#     hh_provides_home_health_aides: Optional[str] = None
#     hh_hospital_readmission_rate: Optional[float] = None
#     hh_home_discharge_success: Optional[float] = None
#     hh_medicare_cost_vs_national_avg: Optional[str] = None


# class FacilityServicesOut(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     offers_alzheimer_dementia_care: Optional[str] = None
#     offers_hospice_care: Optional[str] = None
#     offers_ventilator_care: Optional[str] = None
#     offers_psychiatric_care: Optional[str] = None
#     offers_substance_abuse_treatment: Optional[str] = None
#     offers_hiv_care: Optional[str] = None
#     offers_rehab_services: Optional[str] = None
#     offers_adult_day_care: Optional[str] = None
#     offers_respite_care: Optional[str] = None
#     offers_home_care_services: Optional[str] = None
#     offers_traumatic_brain_injury_care: Optional[str] = None
#     offers_iv_therapy: Optional[str] = None
#     offers_pain_management: Optional[str] = None
#     offers_medical_equipment_supply: Optional[str] = None


# class FacilityDetail(FacilityCard):
#     """Full detail-screen shape -- core + whichever detail table applies."""
#     legal_business_name: Optional[str] = None
#     address: Optional[str] = None
#     county: Optional[str] = None
#     phone: Optional[str] = None
#     email: Optional[str] = None
#     operating_status: Optional[str] = None
#     data_source: Optional[str] = None
#     certification_date: Optional[str] = None
#     secure_memory_care_beds: Optional[int] = None
#     specialty_notes: Optional[str] = None

#     nursing_home_detail: Optional[NursingHomeDetailOut] = None
#     home_health_detail: Optional[HomeHealthDetailOut] = None
#     services: Optional[FacilityServicesOut] = None


# class FacilitySearchParams(BaseModel):
#     state: Optional[str] = Field(default=None, min_length=2, max_length=2)
#     zip_code: Optional[str] = None
#     city: Optional[str] = None
#     facility_type: Optional[str] = None
#     budget_min: Optional[int] = Field(default=None, ge=0)
#     budget_max: Optional[int] = Field(default=None, ge=0)
#     lat: Optional[float] = None
#     lng: Optional[float] = None
#     radius_miles: Optional[float] = Field(default=None, gt=0, le=500)
#     page: int = Field(default=1, ge=1)
#     page_size: int = Field(default=20, ge=1, le=100)


















# """Facility response/request schemas."""
# from typing import Optional

# from app.schemas.common import UUIDStrMixin
# from pydantic import BaseModel, ConfigDict, Field


# class FacilityCard(UUIDStrMixin):
#     """Slim shape for list/search/map/recommended views -- core table only."""
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     name: str
#     facility_type: Optional[str] = None
#     facility_type_category: Optional[str] = None
#     city: Optional[str] = None
#     state: Optional[str] = None
#     zip_code: Optional[str] = None
#     latitude: Optional[float] = None
#     longitude: Optional[float] = None
#     overall_rating: Optional[float] = None
#     bed_count: Optional[int] = None
#     ownership_type: Optional[str] = None


# class FacilitySuggestItem(UUIDStrMixin):
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     name: str
#     city: Optional[str] = None
#     state: Optional[str] = None


# class PaginatedFacilities(BaseModel):
#     items: list[FacilityCard]
#     page: int
#     page_size: int
#     total: int
#     has_more: bool


# class NursingHomeDetailOut(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     nh_total_certified_beds: Optional[int] = None
#     nh_average_daily_residents: Optional[float] = None
#     nh_chain_affiliation: Optional[str] = None
#     nh_ccrc: Optional[str] = None
#     nh_health_inspection_star_rating: Optional[float] = None
#     nh_staffing_star_rating: Optional[float] = None
#     nh_quality_measure_star_rating: Optional[float] = None
#     nh_total_nursing_hours_per_resident_day: Optional[float] = None
#     nh_staff_stability: Optional[str] = None
#     nh_health_deficiencies_latest: Optional[int] = None
#     nh_number_of_fines: Optional[int] = None
#     nh_total_fines_usd: Optional[float] = None
#     nh_penalty_summary: Optional[str] = None


# class HomeHealthDetailOut(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     hh_provides_nursing_care: Optional[str] = None
#     hh_provides_physical_therapy: Optional[str] = None
#     hh_provides_occupational_therapy: Optional[str] = None
#     hh_provides_speech_therapy: Optional[str] = None
#     hh_provides_home_health_aides: Optional[str] = None
#     hh_hospital_readmission_rate: Optional[float] = None
#     hh_home_discharge_success: Optional[float] = None
#     hh_medicare_cost_vs_national_avg: Optional[str] = None


# class FacilityServicesOut(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     offers_alzheimer_dementia_care: Optional[str] = None
#     offers_hospice_care: Optional[str] = None
#     offers_ventilator_care: Optional[str] = None
#     offers_psychiatric_care: Optional[str] = None
#     offers_substance_abuse_treatment: Optional[str] = None
#     offers_hiv_care: Optional[str] = None
#     offers_rehab_services: Optional[str] = None
#     offers_adult_day_care: Optional[str] = None
#     offers_respite_care: Optional[str] = None
#     offers_home_care_services: Optional[str] = None
#     offers_traumatic_brain_injury_care: Optional[str] = None
#     offers_iv_therapy: Optional[str] = None
#     offers_pain_management: Optional[str] = None
#     offers_medical_equipment_supply: Optional[str] = None


# class FacilityDetail(FacilityCard):
#     """Full detail-screen shape -- core + whichever detail table applies."""
#     legal_business_name: Optional[str] = None
#     address: Optional[str] = None
#     county: Optional[str] = None
#     phone: Optional[str] = None
#     email: Optional[str] = None
#     operating_status: Optional[str] = None
#     data_source: Optional[str] = None
#     certification_date: Optional[str] = None
#     secure_memory_care_beds: Optional[int] = None
#     specialty_notes: Optional[str] = None

#     nursing_home_detail: Optional[NursingHomeDetailOut] = None
#     home_health_detail: Optional[HomeHealthDetailOut] = None
#     services: Optional[FacilityServicesOut] = None


# class FacilitySearchParams(BaseModel):
#     state: Optional[str] = Field(default=None, min_length=2, max_length=2)
#     zip_code: Optional[str] = None
#     city: Optional[str] = None
#     facility_type: Optional[str] = None
#     budget_min: Optional[int] = Field(default=None, ge=0)
#     budget_max: Optional[int] = Field(default=None, ge=0)
#     lat: Optional[float] = None
#     lng: Optional[float] = None
#     radius_miles: Optional[float] = Field(default=None, gt=0, le=500)
#     page: int = Field(default=1, ge=1)
#     page_size: int = Field(default=20, ge=1, le=100)






























"""Facility response/request schemas."""
from typing import Optional

from app.schemas.common import UUIDStrMixin
from pydantic import BaseModel, ConfigDict, Field


class FacilityCard(UUIDStrMixin):
    """Slim shape for list/search/map/recommended views -- core table only."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    facility_type: Optional[str] = None
    facility_type_category: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    overall_rating: Optional[float] = None
    bed_count: Optional[int] = None
    ownership_type: Optional[str] = None


class FacilitySuggestItem(UUIDStrMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None


class PaginatedFacilities(BaseModel):
    items: list[FacilityCard]
    page: int
    page_size: int
    total: int
    has_more: bool


class NursingHomeDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    nh_special_focus_facility: Optional[str] = None
    nh_health_inspection_star_rating: Optional[float] = None
    nh_total_nursing_hours_per_resident_day: Optional[float] = None
    nh_total_nursing_staff_turnover_pct: Optional[float] = None


class HomeHealthDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    hh_home_discharge_success: Optional[float] = None
    hh_functional_ability_discharge_score: Optional[float] = None
    hh_falls_major_injury_pct: Optional[float] = None
    hh_developed_bedsores_pct: Optional[float] = None
    hh_hospital_readmission_rate: Optional[float] = None
    hh_started_care_on_time_pct: Optional[float] = None


class FacilityServicesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    offers_alzheimer_dementia_care: Optional[str] = None
    offers_hospice_care: Optional[str] = None
    offers_ventilator_care: Optional[str] = None
    offers_psychiatric_care: Optional[str] = None
    offers_rehab_services: Optional[str] = None
    offers_adult_day_care: Optional[str] = None
    offers_respite_care: Optional[str] = None
    offers_home_care_services: Optional[str] = None
    offers_traumatic_brain_injury_care: Optional[str] = None


class FacilityDetail(FacilityCard):
    """Full detail-screen shape -- core + whichever detail table applies."""
    address: Optional[str] = None
    county: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    facility_subtype: Optional[str] = None
    npi_type: Optional[str] = None
    secure_memory_care_beds: Optional[int] = None
    load_timestamp: Optional[str] = None

    nursing_home_detail: Optional[NursingHomeDetailOut] = None
    home_health_detail: Optional[HomeHealthDetailOut] = None
    services: Optional[FacilityServicesOut] = None


class FacilitySearchParams(BaseModel):
    state: Optional[str] = Field(default=None, min_length=2, max_length=2)
    zip_code: Optional[str] = None
    city: Optional[str] = None
    facility_type: Optional[str] = None
    budget_min: Optional[int] = Field(default=None, ge=0)
    budget_max: Optional[int] = Field(default=None, ge=0)
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_miles: Optional[float] = Field(default=None, gt=0, le=500)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)