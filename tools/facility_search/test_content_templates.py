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

print("--- nursing_home: full attributes ---")
facility = {"ownership_type": "for_profit", "certification_date": date(2015, 3, 1), "name": NAME}
attributes = {
    "certified_beds": 120,
    "avg_residents_per_day": 98,
    "ratings": {"overall": 4, "staffing": 3, "health_inspection": 4, "qm": 4},
    "staffing_hours": {"rn": 0.6, "lpn": 1.2, "nurse_aide": 2.1},
    "total_penalties": 1,
    "number_of_fines": 1,
    "special_focus_status": None,
    "abuse_icon": None,
}
text = build_content("nursing_home", facility, attributes)
print(f"    {text}")
check("name never appears", NAME not in text)
check("mentions for-profit", "for-profit" in text)
check("mentions bed count", "120 certified beds" in text)
check("mentions overall rating", "Overall CMS rating 4/5" in text)
check("mentions RN staffing", "0.6 hours per resident per day" in text)
check("mentions certification year", "certified since 2015" in text)
check("omits null special_focus_status", "Special focus status" not in text)

print("\n--- nursing_home: sparse attributes (nulls degrade gracefully) ---")
sparse = build_content("nursing_home", {"ownership_type": None, "certification_date": None, "name": NAME}, {})
print(f"    {sparse}")
check("no crash on empty attributes", isinstance(sparse, str) and len(sparse) > 0)
check("no 'None' literal leaks into text", "None" not in sparse)
check("name never appears (sparse)", NAME not in sparse)

print("\n--- home_health: full attributes ---")
hh_facility = {"ownership_type": "for_profit", "certification_date": date(2018, 6, 1), "name": "CarePlus"}
hh_attrs = {
    "services": {"nursing": "yes", "physical_therapy": "yes", "occupational_therapy": None},
    "quality_star_rating": 4.5,
    "discharge_to_community_category": "Better Than Expected",
    "readmission_category": None,
    "preventable_hospitalization_category": None,
    "outcomes": {"how_often_patients_improved_walking": "As Expected"},
}
hh_text = build_content("home_health", hh_facility, hh_attrs)
print(f"    {hh_text}")
check("name never appears", "CarePlus" not in hh_text)
check("mentions offered services only", "nursing" in hh_text and "occupational therapy" not in hh_text)
check("mentions quality rating", "4.5/5" in hh_text)
check("mentions discharge category", "Better Than Expected" in hh_text)

print("\n--- hospice: empty attributes schema (_EMPTY) ---")
hospice_facility = {"ownership_type": "nonprofit", "certification_date": date(2015, 1, 1), "name": "Comfort Hospice"}
hospice_text = build_content("hospice", hospice_facility, {})
print(f"    {hospice_text}")
check("name never appears", "Comfort Hospice" not in hospice_text)
check("still produces non-empty content from shared fields", "nonprofit" in hospice_text and "2015" in hospice_text)

print("\n--- irf: empty attributes schema (_EMPTY), no cert date ---")
irf_text = build_content("irf", {"ownership_type": None, "certification_date": None, "name": "Some IRF"}, {})
print(f"    {irf_text}")
check("no crash with nothing available", isinstance(irf_text, str) and len(irf_text) > 0)
check("name never appears", "Some IRF" not in irf_text)
check("uses 'An' before a vowel sound (grammar)", irf_text.startswith("An inpatient"))

print("\n--- ltch: total_beds present ---")
ltch_text = build_content("ltch", {"ownership_type": "government", "certification_date": date(2010, 1, 1)}, {"total_beds": 45})
print(f"    {ltch_text}")
check("mentions bed count", "45 total beds" in ltch_text)
check("mentions government ownership", "government-operated" in ltch_text)

print("\n--- unrecognized facility_type raises loudly (not a silent KeyError) ---")
try:
    build_content("dialysis_center", {}, {})
    failed += 1
    print("  FAIL expected ValueError, none raised")
except ValueError as e:
    passed += 1
    print(f"  OK   raised as expected: {e}")

print("\n--- CONTENT_BUILDERS covers exactly the 5 in-scope facility types ---")
check("registry has exactly 5 types", set(CONTENT_BUILDERS.keys()) == {"nursing_home", "home_health", "hospice", "irf", "ltch"})

print(f"\n{passed} passed, {failed} failed")
if failed:
    raise SystemExit(1)
