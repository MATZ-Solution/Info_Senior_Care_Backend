"""
Pre-ETL sanity check for transforms.py against deliberately messy sample values.
Plain-assertion script (same style as test_agent.py, no new test-framework
dependency). Must pass before run_etl.py ever touches the real ~35k rows.

Run: uv run python -m tools.facility_search.test_transforms
"""
from datetime import date

from tools.facility_search.transforms import (
    zero_pad_5, normalize_ownership, parse_cms_date, to_int, to_float, to_text,
    title_case, upper_trim, passthrough_json,
)

passed = 0
failed = 0


def check(label, actual, expected):
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  OK   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} | got={actual!r} expected={expected!r}")


def check_raises(label, fn, value):
    global passed, failed
    try:
        result = fn(value)
        failed += 1
        print(f"  FAIL {label} | expected raise, got {result!r}")
    except ValueError:
        passed += 1
        print(f"  OK   {label} (raised as expected)")


print("--- zero_pad_5 (ZIP, optional field, must handle ZIP+4) ---")
check("plain 5-digit", zero_pad_5("07030"), "07030")
check("bigint-sourced int", zero_pad_5(7030), "07030")
check("ZIP+4 hyphenated", zero_pad_5("12345-6789"), "12345")
check("ZIP+4 bare (no hyphen)", zero_pad_5("123456789"), "12345")
check("blank returns None (optional)", zero_pad_5(""), None)
check("None returns None (optional)", zero_pad_5(None), None)
check("short numeric (lost leading zeros) gets padded, not rejected", zero_pad_5("123"), "00123")
check_raises("non-numeric raises", zero_pad_5, "ABCDE")

print("\n--- normalize_ownership (ordering bug regression) ---")
check("Non Profit - Corporation -> nonprofit (not for_profit)", normalize_ownership("Non Profit - Corporation"), "nonprofit")
check("For Profit - Individual -> for_profit", normalize_ownership("For Profit - Individual"), "for_profit")
check("Government - State/County -> government", normalize_ownership("Government - State/County"), "government")
check("Non-profit - Church -> nonprofit", normalize_ownership("Non-profit - Church"), "nonprofit")
check("For Profit - Corporation -> for_profit", normalize_ownership("For Profit - Corporation"), "for_profit")
check("blank -> unknown", normalize_ownership(""), "unknown")
check("unrecognized -> unknown", normalize_ownership("Something Else Entirely"), "unknown")

print("\n--- parse_cms_date (optional field: malformed nulls-and-continues at the etl.py layer, not here) ---")
check("MM/DD/YYYY", parse_cms_date("01/15/2020"), date(2020, 1, 15))
check("YYYY-MM-DD", parse_cms_date("2020-01-15"), date(2020, 1, 15))
check("blank -> None", parse_cms_date(""), None)
check("N/A -> None", parse_cms_date("N/A"), None)
check("None -> None", parse_cms_date(None), None)
check_raises("genuinely malformed (non-blank) raises", parse_cms_date, "not-a-date")

print("\n--- to_int / to_float ---")
check("to_int plain", to_int("42"), 42)
check("to_int from float-string", to_int("42.0"), 42)
check("to_int blank -> None", to_int(""), None)
check_raises("to_int garbage raises", to_int, "abc")
check("to_float plain", to_float("1.25"), 1.25)
check("to_float blank -> None", to_float(None), None)
check_raises("to_float garbage raises", to_float, "abc")

print("\n--- to_text ---")
check("strips whitespace", to_text("  Sunrise Manor  "), "Sunrise Manor")
check("blank -> None", to_text(""), None)
check("N/A -> None", to_text("N/A"), None)

print("\n--- title_case (Phase 11 -- city/county casing) ---")
check("all caps -> title case", title_case("PHOENIX"), "Phoenix")
check("all lower -> title case", title_case("phoenix"), "Phoenix")
check("multi-word", title_case("  new york city  "), "New York City")
check("blank -> None", title_case(""), None)
check("None -> None", title_case(None), None)

print("\n--- upper_trim (Phase 11 -- state casing) ---")
check("lowercase -> upper", upper_trim("az"), "AZ")
check("mixed case with whitespace", upper_trim("  Az "), "AZ")
check("blank -> None", upper_trim(""), None)
check("None -> None", upper_trim(None), None)

print("\n--- passthrough_json (Phase 11 -- opaque source_extra catch-all) ---")
check("JSON object STRING (asyncpg's real jsonb-without-codec shape) parses to dict",
      passthrough_json('{"a": 1}'), {"a": 1})
check("JSON array string parses to list", passthrough_json("[1, 2]"), [1, 2])
check("already-decoded dict passes through unchanged (defensive)", passthrough_json({"a": 1}), {"a": 1})
check("already-decoded list passes through unchanged (defensive)", passthrough_json([1, 2]), [1, 2])
check("blank -> None", passthrough_json(""), None)
check("None -> None", passthrough_json(None), None)
check_raises("non-JSON garbage string raises", passthrough_json, "not{valid]json")

print(f"\n{passed} passed, {failed} failed")
if failed:
    raise SystemExit(1)
