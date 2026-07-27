"""
Phase 4 -- Stage 2 fuzzy correction (architecture docs, Section 6): pg_trgm
similarity lookups against the small closed facility_type vocabulary and the
larger open city/state vocabulary, using the GIN trigram indexes already
created in schema.py (idx_facility_type_aliases_trgm, idx_known_values_trgm).

Two differently-weighted confidence thresholds, deliberate, not accidental:
  - FACILITY_TYPE_CONFIDENCE is strict -- facility type is a small closed
    vocabulary (5 values today), so a low-confidence match is asked about
    rather than silently applied ("ask, don't guess," Section 6).
  - KNOWN_VALUE_CONFIDENCE is lenient -- city/state is a large open
    vocabulary of real-world names, so a low-confidence match is just dropped
    from the filter rather than blocking the whole search. A wrong-but-
    plausible city guess is a much smaller failure mode than a wrong facility
    type, which is why these two thresholds are NOT the same value.

Explicitly deferred, not built here: the docs' top-2 confidence-*gap* check
(disambiguating e.g. "Prescott" vs. "Prescott Valley"). This uses a simple
absolute-threshold check only -- a known, documented limitation, not an
oversight.

Real finding from running this against live data (not assumed on paper):
infomary_known_values.value for field='state' stores CMS's native 2-letter
codes ("AZ"), not full names ("Arizona") -- confirmed via direct query. But
real users type full state names in conversation. Trigram-matching a full
name (or a typo of one, e.g. "arizna") against a 2-letter code scores far too
low to ever resolve (similarity('arizna','AZ') = 0.11). STATE_ABBREVIATIONS
below handles the full-name case in Python (no DB round trip needed for a
static 51-entry list) before falling through to the generic trigram path,
which still catches typo'd/miscased abbreviations ("Ariz", "az").
"""
import difflib

FACILITY_TYPE_CONFIDENCE = 0.4
KNOWN_VALUE_CONFIDENCE = 0.3
STATE_NAME_CONFIDENCE = 0.6  # full-name typo tolerance; stricter than KNOWN_VALUE_CONFIDENCE since a wrong state is a bigger miss than a wrong city

STATE_ABBREVIATIONS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR",
}


def _resolve_state_name(raw_value: str) -> tuple[str | None, float]:
    cleaned = raw_value.strip().lower()
    if cleaned.upper() in STATE_ABBREVIATIONS.values():
        return cleaned.upper(), 1.0
    if cleaned in STATE_ABBREVIATIONS:
        return STATE_ABBREVIATIONS[cleaned], 1.0
    match = difflib.get_close_matches(cleaned, STATE_ABBREVIATIONS.keys(), n=1, cutoff=STATE_NAME_CONFIDENCE)
    if match:
        return STATE_ABBREVIATIONS[match[0]], difflib.SequenceMatcher(None, cleaned, match[0]).ratio()
    return None, 0.0


async def correct_facility_type(conn, raw_value: str) -> tuple[str | None, float]:
    if not raw_value or not raw_value.strip():
        return None, 0.0
    row = await conn.fetchrow(
        "SELECT type_key, similarity(alias, $1) AS sim FROM infomary_facility_type_aliases "
        "WHERE alias % $1 ORDER BY sim DESC LIMIT 1",
        raw_value.strip().lower(),
    )
    if not row:
        return None, 0.0
    return row["type_key"], row["sim"]


async def correct_known_value(conn, field: str, raw_value: str) -> tuple[str | None, float]:
    if not raw_value or not raw_value.strip():
        return None, 0.0

    if field == "state":
        resolved, score = _resolve_state_name(raw_value)
        if resolved:
            return resolved, score
        # fall through to the generic trigram path below -- catches a
        # miscased/typo'd abbreviation ("Ariz", "az") that _resolve_state_name
        # didn't match against a full name.

    row = await conn.fetchrow(
        "SELECT value, similarity(value, $2) AS sim FROM infomary_known_values "
        "WHERE field = $1 AND value % $2 ORDER BY sim DESC LIMIT 1",
        field, raw_value.strip(),
    )
    if not row:
        return None, 0.0
    return row["value"], row["sim"]
