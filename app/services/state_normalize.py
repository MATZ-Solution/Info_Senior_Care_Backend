"""
US state normalization.

The `state` column in `facilities` stores 2-letter USPS abbreviations only
(e.g. "CA", "TX") -- that's what the source data uses. But a search text
box realistically gets typed as "California", "california", "Calif.",
"CA", etc. This module normalizes whatever the user typed into the
2-letter form the database actually stores, so the search filter works
regardless of which form they use.
"""

STATE_NAME_TO_ABBR: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "washington dc": "DC",
    "florida": "FL", "georgia": "GA", "guam": "GU", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "puerto rico": "PR", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

_VALID_ABBRS = set(STATE_NAME_TO_ABBR.values())


def normalize_state(raw: str | None) -> str | None:
    """
    Accepts "California", "california", "CA", "ca" (or None) and returns
    the 2-letter abbreviation the DB actually stores ("CA"), or None if
    the input is empty or unrecognized. Callers should treat None as
    "no valid state filter" rather than raising -- an unrecognized state
    name typed by the user shouldn't 500 the request, just not filter.
    """
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None

    upper = cleaned.upper()
    if len(upper) == 2 and upper in _VALID_ABBRS:
        return upper

    return STATE_NAME_TO_ABBR.get(cleaned.lower())
