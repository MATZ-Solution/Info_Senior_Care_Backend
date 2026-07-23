"""
Pre-embedding sanity check for content_templates.py (same plain-assertion style
as test_transforms.py). Must pass before run_embed_sync.py ever spends a real
Fireworks API call.

Run: uv run python -m tools.facility_search.test_content_templates
"""
from datetime import date

from tools.facility_search.content_templates import build_content, CONTENT_BUILDERS

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


NAME = "Sunrise Manor"  # must never appear in generated content (Section 5)

print("--- nursing_home: full attributes (Phase 11 field shape) ---")
facility = {"ownership_type": "for_profit", "certification_date": date(2015, 3, 1), "name": NAME}
attributes = {
    "overall_rating": 4,
    "total_certified_beds": 120,
    "chain_affiliation": "Golden Care Group",
    "health_inspection_rating": 4,
    "staffing_rating": 3,
    "quality_measure_rating": 4,
    "staffing_level_assessment": "Above average",
    "offers": {"alzheimer_dementia_care": "yes", "iv_therapy": None},
}
text = build_content("nursing_home", facility, attributes)
print(f"    {text}")
check("name never appears", NAME not in text)
check("mentions for-profit", "for-profit" in text)
check("mentions bed count", "120 certified beds" in text)
check("mentions overall rating", "Overall rating 4/5" in text)
check("mentions staffing rating", "Staffing 3/5" in text)
check("mentions chain affiliation", "Golden Care Group" in text)
check("mentions certification year", "certified since 2015" in text)
check("mentions shared offers", "alzheimer dementia care" in text)
check("omits null iv_therapy", "iv therapy" not in text)

print("\n--- nursing_home: sparse attributes (nulls degrade gracefully) ---")
sparse = build_content("nursing_home", {"ownership_type": None, "certification_date": None, "name": NAME}, {})
print(f"    {sparse}")
check("no crash on empty attributes", isinstance(sparse, str) and len(sparse) > 0)
check("no 'None' literal leaks into text", "None" not in sparse)
check("name never appears (sparse)", NAME not in sparse)

print("\n--- home_health: full attributes (Phase 11 field shape) ---")
hh_facility = {"ownership_type": "for_profit", "certification_date": date(2018, 6, 1), "name": "CarePlus"}
hh_attrs = {
    "overall_rating": 4.5,
    "offers_nursing_care": "yes",
    "offers_physical_therapy": "yes",
    "offers_occupational_therapy": None,
    "home_discharge_success": 0.82,
}
hh_text = build_content("home_health", hh_facility, hh_attrs)
print(f"    {hh_text}")
check("name never appears", "CarePlus" not in hh_text)
check("mentions offered services only", "nursing care" in hh_text and "occupational therapy" not in hh_text)
check("mentions overall rating", "Overall rating 4.5/5" in hh_text)
check("mentions discharge success", "0.82" in hh_text)

print("\n--- hospice: shared-base-only schema ---")
hospice_facility = {"ownership_type": "nonprofit", "certification_date": date(2015, 1, 1), "name": "Comfort Hospice"}
hospice_text = build_content("hospice", hospice_facility, {"overall_rating": 5})
print(f"    {hospice_text}")
check("name never appears", "Comfort Hospice" not in hospice_text)
check("mentions ownership + rating + year", "nonprofit" in hospice_text and "5/5" in hospice_text and "2015" in hospice_text)

print("\n--- irf: no attributes at all, no cert date ---")
irf_text = build_content("irf", {"ownership_type": None, "certification_date": None, "name": "Some IRF"}, {})
print(f"    {irf_text}")
check("no crash with nothing available", isinstance(irf_text, str) and len(irf_text) > 0)
check("name never appears", "Some IRF" not in irf_text)
check("uses 'An' before a vowel sound (grammar)", irf_text.startswith("An inpatient"))

print("\n--- ltch: total_beds present (historical/unused, retired type) ---")
ltch_text = build_content("ltch", {"ownership_type": "government", "certification_date": date(2010, 1, 1)}, {"total_beds": 45})
print(f"    {ltch_text}")
check("mentions bed count", "45 total beds" in ltch_text)
check("mentions government ownership", "government-operated" in ltch_text)

print("\n--- assisted_living (Phase 11): shared base only ---")
al_facility = {"ownership_type": "for_profit", "certification_date": date(2019, 4, 1), "name": "Golden Years"}
al_attrs = {"offers": {"alzheimer_dementia_care": "yes", "respite_care": "yes", "iv_therapy": None}}
al_text = build_content("assisted_living", al_facility, al_attrs)
print(f"    {al_text}")
check("name never appears", "Golden Years" not in al_text)
check("mentions offered flags only", "alzheimer dementia care" in al_text and "iv therapy" not in al_text)
check("mentions certification year", "certified since 2019" in al_text)

print("\n--- nursing_staffing_agency (Phase 11): must NOT read like a care facility ---")
nsa_text = build_content("nursing_staffing_agency", {"ownership_type": "for_profit", "certification_date": None, "name": "ProNurse Staffing"}, {})
print(f"    {nsa_text}")
check("name never appears", "ProNurse Staffing" not in nsa_text)
check("explicitly states it's a staffing vendor, not a residence", "staffing vendor" in nsa_text and "not a residence" in nsa_text)

print("\n--- outpatient_rehab (Phase 11): empty attributes degrades gracefully ---")
or_text = build_content("outpatient_rehab", {"ownership_type": None, "certification_date": None, "name": "Some Rehab"}, {})
print(f"    {or_text}")
check("no crash with nothing available", isinstance(or_text, str) and len(or_text) > 0)
check("name never appears", "Some Rehab" not in or_text)

print("\n--- unrecognized facility_type raises loudly (not a silent KeyError) ---")
try:
    build_content("dialysis_hmo_typo", {}, {})
    failed += 1
    print("  FAIL expected ValueError, none raised")
except ValueError as e:
    passed += 1
    print(f"  OK   raised as expected: {e}")

print("\n--- CONTENT_BUILDERS covers exactly the 16 known facility types (15 active + retired ltch) ---")
check("registry has exactly 16 types", set(CONTENT_BUILDERS.keys()) == {
    "nursing_home", "home_health", "hospice", "irf", "ltch",
    "assisted_living", "icf_iid", "home_care", "adult_day_care",
    "behavioral_health", "outpatient_rehab", "hospital", "dialysis_center",
    "ambulatory_surgery_center", "nursing_staffing_agency", "other_specialty",
})

print(f"\n{passed} passed, {failed} failed")
if failed:
    raise SystemExit(1)
