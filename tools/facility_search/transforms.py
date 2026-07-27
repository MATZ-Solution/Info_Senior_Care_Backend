"""
Named transform-function registry for the facility ETL.

Each transform takes a raw source value and returns the normalized value (or None
for genuinely absent/blank data), or raises ValueError on input it cannot make
sense of. Required-vs-optional handling (reject the whole row vs. null just this
field) is NOT decided here -- it lives in etl.py, driven by
infomary_source_field_mappings.is_required, so the same transform can back both a
required field (name) and an optional one (zip_code) without duplicating that
policy in every function.

No CCN-format transform lives here (there was one, zero_pad_6 -- deleted). CCN is
no longer facility_id or format-validated at all; it's a plain to_text-mapped
audit column (source_identifier) that feeds facility_id's uuid5 hash as opaque
bytes in etl.py, corrupted-looking or not. Format-validating an identifier that's
never displayed or queried was the direct cause of a real bug (real CMS CCNs are
alphanumeric, e.g. "A01500", which a digits-only assumption rejected wholesale).
"""
import json
from datetime import date, datetime


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in ("", "N/A", "n/a", "None", "none"))


def zero_pad_5(value) -> str | None:
    """
    ZIP -> 5-digit text. Handles ZIP+4 in hyphenated ("12345-6789") or bare
    ("123456789") form by taking the first 5 digits. Blank input returns None
    (zip_code is optional); genuinely unusable input raises so etl.py can null
    the field per the optional-field rule.
    """
    if _blank(value):
        return None
    raw = str(value).strip().replace("-", "")
    if not raw.isdigit():
        raise ValueError(f"ZIP is not numeric: {value!r}")
    # <=5 digits: a short numeric ZIP is presumably missing leading zeros (the same
    # bigint zero-loss as CCN) -- zero-pad it, don't reject it. >5 digits: ZIP+4,
    # take the first 5 (already full-width, no padding needed).
    return raw.zfill(5) if len(raw) <= 5 else raw[:5]


def to_text(value) -> str | None:
    if _blank(value):
        return None
    return str(value).strip()


passthrough = to_text


def title_case(value) -> str | None:
    """
    city/county casing normalizer -- Phase 11's combined source is not
    guaranteed pre-cased like the 5 original CMS exports were. Consistent
    casing matters downstream: known_values dedup (etl.py's
    _refresh_known_values) and fuzzy_match.py's trigram lookups both key on
    the literal stored string, so "Phoenix" and "PHOENIX" would otherwise
    dedup and match as two different values.
    """
    if _blank(value):
        return None
    return str(value).strip().title()


def upper_trim(value) -> str | None:
    """
    state casing normalizer -- fuzzy_match.py's STATE_ABBREVIATIONS/
    _resolve_state_name assume the stored value is an uppercase 2-letter code
    ("AZ"), matching the 5 original CMS tables' native format. Uppercasing
    here keeps that assumption true regardless of the combined source's
    actual casing.
    """
    if _blank(value):
        return None
    return str(value).strip().upper()


def passthrough_json(value):
    """
    For source_extra -- an opaque catch-all JSON blob column. database.py's
    asyncpg.create_pool has no `init=` callback registering a jsonb type
    codec (confirmed by reading it directly), so a jsonb/json source column
    comes back from asyncpg as a raw JSON *string*, not an already-decoded
    dict/list -- confirmed, not assumed. Parsing it here is required: passing
    the raw string straight through (deliberately NOT to_text either, which
    would stringify a dict via repr() instead) would let etl.py's
    _detail_params double-encode it later (a JSON string sitting inside the
    JSON string) instead of storing a real nested object. Also accepts an
    already-decoded dict/list unchanged, defensively, in case a codec is ever
    added later.
    """
    if _blank(value):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (ValueError, TypeError):
        raise ValueError(f"source_extra is not valid JSON: {value!r}")


def to_int(value) -> int | None:
    if _blank(value):
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        raise ValueError(f"Not a valid integer: {value!r}")


def to_float(value) -> float | None:
    if _blank(value):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        raise ValueError(f"Not a valid float: {value!r}")


# Ordering matters -- this is a known real bug, not a hypothetical. CMS ownership
# strings share vocabulary across categories: "Non Profit - Corporation" contains
# "Corporation," a word that also appears in for-profit entity names. A naive
# first-match-wins normalizer that checks for_profit needles first misclassifies
# it. nonprofit/government must be checked before for_profit, which is deliberately
# the broadest/last bucket.
_NONPROFIT_NEEDLES = ("non profit", "non-profit", "nonprofit", "church", "voluntary")
_GOVERNMENT_NEEDLES = ("government", "county", "state/county", "hospital district", "city/county")
_FOR_PROFIT_NEEDLES = ("for profit", "for-profit", "proprietary", "individual", "partnership", "corporation", "llc")


def normalize_ownership(value) -> str:
    if _blank(value):
        return "unknown"
    text = str(value).strip().lower()
    if any(n in text for n in _NONPROFIT_NEEDLES):
        return "nonprofit"
    if any(n in text for n in _GOVERNMENT_NEEDLES):
        return "government"
    if any(n in text for n in _FOR_PROFIT_NEEDLES):
        return "for_profit"
    return "unknown"


_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d")


def parse_cms_date(value) -> date | None:
    if _blank(value):
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {value!r}")


TRANSFORMS = {
    "zero_pad_5": zero_pad_5,
    "to_text": to_text,
    "passthrough": passthrough,
    "to_int": to_int,
    "to_float": to_float,
    "normalize_ownership": normalize_ownership,
    "parse_cms_date": parse_cms_date,
    "title_case": title_case,
    "upper_trim": upper_trim,
    "passthrough_json": passthrough_json,
}
