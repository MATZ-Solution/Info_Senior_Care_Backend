"""
Row -> TypeSense document mapping for `public."All_State_Type_combined"`.

Pure functions only: no database, no network, no TypeSense client. That makes
the whole mapping layer unit-testable against fixture rows, which matters more
here than usual because the source table is almost entirely untyped.

WHY THIS FILE IS NOT TRIVIAL
----------------------------
96 of the source table's 98 columns are `text`, including every number:
`latitude`, `longitude`, `bed_count`, `overall_rating`, `cms_region`. They
arrive as strings like "38.3071606", "" or "1,200". TypeSense is strictly
typed — handing it "38.3071606" for a `float` field rejects the document. So
every numeric field must be parsed and validated here, and a value that cannot
be parsed becomes absent rather than zero: a facility with an unparseable bed
count has an UNKNOWN bed count, not zero beds.

The source data also uses several different ways of saying "no value": empty
string, the literal "unknown", "N/A", "-". All of them must normalize to
absent, or "unknown" ends up as a facet value the user can click on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

logger = logging.getLogger("app.typesense.mapper")

# Source table name — one place, since the identifier is case-sensitive and
# must be double-quoted in every SQL statement that touches it.
SOURCE_TABLE = 'public."All_State_Type_combined"'

# Columns actually read from the source row. Everything else in the 98-column
# table is deliberately ignored: only fields backing the existing API contract
# (filters + FacilityCard response + the searchable text fields) are indexed.
SOURCE_COLUMNS: tuple[str, ...] = (
    "uuid",
    "name",
    "address",
    "city",
    "state",
    "zip_code",
    "facility_type",
    "facility_type_category",
    "ownership_type",
    "latitude",
    "longitude",
    "overall_rating",
    "bed_count",
    "updated_at",
)

# Case-insensitive placeholders that mean "no value" in this dataset.
_NULL_TOKENS = frozenset(
    {"", "unknown", "n/a", "na", "null", "none", "-", "--", "not available", "nan"}
)

# Sanity bounds. A row with latitude 999 is corrupt, not a real place; letting
# it through puts a map pin in the ocean.
_LAT_RANGE = (-90.0, 90.0)
_LNG_RANGE = (-180.0, 180.0)


# --------------------------------------------------------------------------
# Coercion primitives
# --------------------------------------------------------------------------


def clean_str(value: Any) -> str | None:
    """Trim, collapse placeholder tokens to None. Returns None or a non-empty str."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in _NULL_TOKENS:
        return None
    return text


def to_float(value: Any) -> float | None:
    """
    Parse a possibly-messy numeric string. Returns None rather than raising —
    one bad cell must not cost us the whole document.
    """
    text = clean_str(value)
    if text is None:
        return None
    # Thousands separators and currency symbols show up in CMS exports.
    text = text.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    # NaN/inf are valid Python floats but not valid JSON — they would blow up
    # serialization on the way to TypeSense.
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def to_int(value: Any) -> int | None:
    """
    Parse to int via float, so "120.0" works as well as "120". TypeSense
    `int32` rejects anything outside 32-bit range, so clamp-check here.
    """
    parsed = to_float(value)
    if parsed is None:
        return None
    as_int = int(parsed)
    if not (-2_147_483_648 <= as_int <= 2_147_483_647):
        return None
    return as_int


def to_epoch_seconds(value: Any) -> int | None:
    """
    Datetime -> epoch seconds (TypeSense has no native date type).

    `created_at`/`updated_at` are `timestamp without time zone`, i.e. naive.
    They are treated as UTC, which is correct as long as every writer uses the
    same database clock — and the `set_updated_at` trigger guarantees that for
    updates.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    text = clean_str(value)
    if text is None:
        return None
    try:
        # Handles "2026-07-22 09:39:30.972483" and ISO-8601 with offsets.
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.debug("Unparseable timestamp: %r", text)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _coord(value: Any, bounds: tuple[float, float]) -> float | None:
    """A coordinate is only usable if it parses AND lies inside valid bounds."""
    parsed = to_float(value)
    if parsed is None:
        return None
    low, high = bounds
    if not (low <= parsed <= high):
        return None
    # Exactly (0, 0) is Null Island — in US facility data it always means
    # "geocoding failed", never a real location.
    return parsed


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------


class MappingError(ValueError):
    """A row cannot become a valid document (missing id or name)."""


def to_document(row: Mapping[str, Any]) -> dict[str, Any]:
    """
    Convert one source row into a TypeSense document.

    Only non-None values are included: TypeSense treats an absent key as "field
    missing" for an `optional` field, whereas an explicit null is a type error.
    That is why this builds the dict conditionally instead of assigning None.

    Raises:
        MappingError: no `uuid` (nothing to key the document on) or no `name`
            (a nameless facility is unfindable and unusable in results).
    """
    doc_id = clean_str(row.get("uuid"))
    if doc_id is None:
        raise MappingError("row has no `uuid` — cannot build a document id")

    name = clean_str(row.get("name"))
    if name is None:
        raise MappingError(f"facility {doc_id} has no `name` — refusing to index")

    doc: dict[str, Any] = {
        "id": doc_id,
        "name": name,
    }

    # --- optional strings ---
    #  `state` is upper-cased so the filter is deterministic: the source mixes
    #  "CA" with the occasional lower-cased value, and TypeSense `filter_by`
    #  string equality is case-sensitive (unlike its search matching).
    string_fields = {
        "address": clean_str(row.get("address")),
        "city": clean_str(row.get("city")),
        "state": (clean_str(row.get("state")) or "").upper() or None,
        "zip_code": clean_str(row.get("zip_code")),
        "facility_type": clean_str(row.get("facility_type")),
        "facility_type_category": clean_str(row.get("facility_type_category")),
        "ownership_type": clean_str(row.get("ownership_type")),
    }
    doc.update({key: val for key, val in string_fields.items() if val is not None})

    # --- geo ---
    latitude = _coord(row.get("latitude"), _LAT_RANGE)
    longitude = _coord(row.get("longitude"), _LNG_RANGE)
    # Both or neither. A lone latitude is useless and would render a broken pin.
    if latitude is not None and longitude is not None and not (latitude == 0.0 and longitude == 0.0):
        doc["latitude"] = latitude
        doc["longitude"] = longitude

    # --- numerics ---
    rating = to_float(row.get("overall_rating"))
    if rating is not None:
        doc["overall_rating"] = rating

    beds = to_int(row.get("bed_count"))
    if beds is not None:
        doc["bed_count"] = beds

    # Always present — backs `default_sorting_field`, and reproduces Postgres's
    # `ORDER BY overall_rating DESC NULLS LAST` (unrated sinks to the bottom).
    doc["rating_sort"] = rating if rating is not None else 0.0

    updated_at = to_epoch_seconds(row.get("updated_at"))
    if updated_at is not None:
        doc["updated_at"] = updated_at

    return doc


def to_documents(rows: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Map a batch, isolating failures.

    Returns:
        (documents, errors) — one unmappable row must never abort a 60k import,
        so bad rows are collected and reported instead of raised. The caller
        logs the errors and still ships the good documents.
    """
    documents: list[dict[str, Any]] = []
    errors: list[str] = []

    for row in rows:
        try:
            documents.append(to_document(row))
        except MappingError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 — never let one row kill a batch
            errors.append(f"unexpected mapping failure: {exc!r}")

    return documents, errors


def build_select_sql() -> str:
    """
    The single SELECT used by both the bulk import and the incremental sync.

    Lives here next to SOURCE_COLUMNS so the column list can never drift
    between "what we query" and "what we map" — that drift is silent (the
    mapper just sees a missing key and omits the field), so it is worth the
    slight layering compromise of putting a SQL string in a mapper module.

    Named parameters, all nullable, so one statement covers every mode:

        :after_uuid  keyset cursor — NULL starts from the beginning
        :since       only rows changed after this timestamp — NULL means all
        :limit       batch size

    KEYSET, NOT OFFSET. `LIMIT/OFFSET` over 60k rows degrades on every page
    (Postgres still walks the skipped rows), and worse: if the upstream
    pipeline inserts while a reindex is running, OFFSET pagination silently
    skips and duplicates rows. Ordering by the primary key and carrying the
    last seen value forward is stable under concurrent writes.
    """
    columns = ", ".join(f'"{col}"' for col in SOURCE_COLUMNS)
    return (  # noqa: S608 — identifiers are hardcoded; values are bound params
        f"SELECT {columns} "
        f"FROM {SOURCE_TABLE} "
        f'WHERE (CAST(:after_uuid AS text) IS NULL OR "uuid" > CAST(:after_uuid AS text)) '
        f'  AND (CAST(:since AS timestamp) IS NULL OR "updated_at" > CAST(:since AS timestamp)) '
        f'ORDER BY "uuid" '
        f"LIMIT :limit"
    )


def build_count_sql() -> str:
    """Row count for the same filter, so the import can report real progress."""
    return (  # noqa: S608 — see build_select_sql
        f"SELECT COUNT(*) FROM {SOURCE_TABLE} "
        f'WHERE (CAST(:since AS timestamp) IS NULL OR "updated_at" > CAST(:since AS timestamp))'
    )


def build_all_ids_sql() -> str:
    """
    Every live primary key, for deletion reconciliation.

    An `updated_at` watermark can never reveal a DELETE — the row is simply
    gone, so nothing shows up as "changed". Without this the index keeps
    serving facilities that no longer exist.
    """
    return f'SELECT "uuid" FROM {SOURCE_TABLE}'  # noqa: S608 — see build_select_sql