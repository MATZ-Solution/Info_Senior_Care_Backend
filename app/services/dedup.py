"""
Deduplication key computation.

This is the SINGLE source of truth for how we decide "is this the same
facility as one we already have" when ccn is not available. Both the
import script (scripts/import_facilities.py) and any future manual-insert
path MUST use this exact function, or hashes won't match existing rows and
"duplicates" will silently start appearing.
"""
import hashlib
import re

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip().lower()
    value = _NON_ALNUM_RE.sub("", value)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def compute_dedup_hash(
    name: str | None,
    address: str | None,
    zip_code: str | None,
    state: str | None,
    facility_type: str | None,
) -> str:
    """
    Deterministic fallback identity key: normalize(name)|normalize(address)|
    zip|state|facility_type. Used to match incoming rows against existing
    facilities when `ccn` is absent (roughly 81% of current rows), so
    re-importing the same or an overlapping file never creates duplicate
    facility rows.

    `facility_type` is included deliberately: real data shows the same
    business name + address legitimately operating multiple distinct
    licensed service lines at once (e.g. "Bartlett Home Health and Hospice"
    holds a separate Home Health license AND a separate Hospice license,
    often with different CCNs). Those are different facility records in
    this app, not duplicates of each other -- excluding facility_type from
    the key would incorrectly collapse them into one row.
    """
    parts = [
        _normalize(name),
        _normalize(address),
        _normalize(zip_code),
        _normalize(state),
        _normalize(facility_type),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
