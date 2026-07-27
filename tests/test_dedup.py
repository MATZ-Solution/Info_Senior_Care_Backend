"""
Unit tests for the dedup hash logic -- this is the core guarantee behind
"future file imports never create duplicates". If these break, the import
pipeline's idempotency guarantee breaks with them.
"""
from app.services.dedup import compute_dedup_hash


def test_same_inputs_produce_same_hash():
    h1 = compute_dedup_hash("Sunrise Manor", "123 Main St", "90210", "CA", "Nursing Home")
    h2 = compute_dedup_hash("Sunrise Manor", "123 Main St", "90210", "CA", "Nursing Home")
    assert h1 == h2


def test_case_and_whitespace_insensitive():
    h1 = compute_dedup_hash("Sunrise Manor", "123 Main St", "90210", "CA", "Nursing Home")
    h2 = compute_dedup_hash("  sunrise   manor  ", "123 MAIN st", "90210", "ca", "nursing home")
    assert h1 == h2


def test_different_facility_type_produces_different_hash():
    """
    Same name/address/zip/state but different facility_type must NOT
    collide -- real data has businesses running both a Home Health AND a
    Hospice license at the same address; these are distinct facilities.
    """
    h1 = compute_dedup_hash("Bartlett Home Health and Hospice", "641 Willoughby Ave", "99801", "AK", "Home Health")
    h2 = compute_dedup_hash("Bartlett Home Health and Hospice", "641 Willoughby Ave", "99801", "AK", "Hospice")
    assert h1 != h2


def test_different_address_produces_different_hash():
    h1 = compute_dedup_hash("Sunrise Manor", "123 Main St", "90210", "CA", "Nursing Home")
    h2 = compute_dedup_hash("Sunrise Manor", "456 Oak Ave", "90210", "CA", "Nursing Home")
    assert h1 != h2


def test_missing_fields_handled_gracefully():
    # Must not raise even with all-None input.
    h = compute_dedup_hash(None, None, None, None, None)
    assert isinstance(h, str) and len(h) == 64
