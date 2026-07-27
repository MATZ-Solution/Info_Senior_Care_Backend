"""
ETL engine for All_State_Type_combined, the single active source (Phase 11).
Reads its mapping rows from infomary_source_field_mappings (never mappings.py
directly), reads the raw source table, applies transforms.py, resolves each
row's facility_type from facility_type_category, validates
facility_detail.attributes against schemas.py, and upserts into
infomary_facilities + infomary_facility_detail.

Performance design (revised after actually running this against live Supabase --
the first cut did one existing-row SELECT plus a savepoint per row, which at
observed ~0.36s/statement network latency to a hosted Postgres instance worked
out to 10+ hours for ~35k rows):

1. Prefetch existing (facility_id, facility_type, source_row_hash) for the
   table's candidate facility_ids in ONE query -- collision/unchanged/to-upsert
   is then a dict lookup per row, not a query per row.
2. Batch the actual upserts via executemany() in chunks of BATCH_SIZE. executemany
   pipelines each row as its own statement execution rather than building one
   multi-row VALUES(...) list -- this deliberately avoids Postgres's "ON CONFLICT
   DO UPDATE command cannot affect row a second time" error, which a hand-rolled
   multi-row VALUES statement WOULD hit if a batch happened to contain two rows
   with the same facility_id (a real possibility -- duplicate ids within the
   source table are already deduplicated before batching, see
   _process_combined_table, but executemany's per-statement semantics make this
   safe either way).
3. Batch failure does NOT reject the whole batch: pre-validation (transforms +
   JSON Schema) already ran before any row reaches batching, so a batch failing at
   the DB level is an unexpected residual case -- it falls back to per-row
   execution for just that batch, isolating the one bad row instead of losing
   every good row alongside it.
4. Per-batch progress logging (batch number, size, cumulative counters, elapsed
   time) -- there is otherwise no visibility into progress until the whole
   table's transaction commits, which is exactly what made "is this hung or
   just slow" unanswerable last time. This matters even more here: this table
   is ~65k+ rows, larger than any of the original 5 tables individually.

Identity design: facility_id is a direct passthrough of the source's own
`uuid` column (Phase 11 -- confirmed stable across re-exports/appends by the
data owner), not a computed hash. CCN is never format-validated -- it's a
plain audit column (source_identifier), same role it always had.
"""
import hashlib
import json
import time
import uuid

from jsonschema import validate, ValidationError

from database import get_db_connection
from logger import log_db, log_success, log_warn, log_error
from tools.facility_search.transforms import TRANSFORMS
from tools.facility_search.schemas import SCHEMAS
from tools.facility_search.mappings import (
    COMBINED_TABLE, FACILITY_TYPE_CATEGORY_RESOLUTION, KNOWN_CATEGORIES,
)

FACILITIES_COLUMNS = [
    "name", "address_line1", "address_line2", "city", "state", "zip_code",
    "county", "phone", "ownership_type", "certification_date", "cms_region",
    "latitude", "longitude", "source_identifier",
    # Phase 11 -- new on All_State_Type_combined, absent from the 5 retired tables.
    "legal_business_name", "email", "facility_subtype",
]

BATCH_SIZE = 500

# COMBINED_MAPPINGS maps these unconditionally for every row (see
# _process_combined_table's Pass 1 for why they must be stripped per-row
# based on resolved facility_type before schema validation).
_NURSING_HOME_ONLY_KEYS = frozenset({
    "total_certified_beds", "chain_affiliation", "health_inspection_rating",
    "staffing_rating", "quality_measure_rating", "staffing_level_assessment",
})
_HOME_HEALTH_ONLY_KEYS = frozenset({
    "offers_nursing_care", "offers_physical_therapy", "offers_occupational_therapy",
    "offers_speech_therapy", "offers_medical_social_services", "offers_home_health_aides",
    "home_discharge_success",
})

_FACILITIES_UPSERT_SQL = f"""
    INSERT INTO infomary_facilities (
        facility_id, facility_type, {", ".join(FACILITIES_COLUMNS)},
        source_table, source_row_hash
    ) VALUES (
        $1, $2, {", ".join(f"${i}" for i in range(3, 3 + len(FACILITIES_COLUMNS)))},
        ${3 + len(FACILITIES_COLUMNS)}, ${4 + len(FACILITIES_COLUMNS)}
    )
    ON CONFLICT (facility_id) DO UPDATE SET
        {", ".join(f"{c}=EXCLUDED.{c}" for c in FACILITIES_COLUMNS)},
        source_row_hash = EXCLUDED.source_row_hash
"""

_DETAIL_UPSERT_SQL = """
    INSERT INTO infomary_facility_detail (facility_id, attributes)
    VALUES ($1, $2::jsonb)
    ON CONFLICT (facility_id) DO UPDATE SET attributes = EXCLUDED.attributes
"""


class _RowRejected(Exception):
    pass


def _set_nested(d: dict, dotted_key: str, value):
    parts = dotted_key.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _canonical_hash(facility_type: str, facility: dict, attributes: dict) -> str:
    payload = json.dumps(
        {"facility_type": facility_type, "facility": facility, "attributes": attributes},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _build_record(row_dict: dict, table_mappings: list, source_table: str):
    """
    Pure-Python transform pass, no DB access. Raises _RowRejected if a required
    field's transform fails or ends up blank; nulls an optional field on the same
    failure and continues. Returns (facility_dict, attributes_dict).

    Dotted target_field values (e.g. "offers.alzheimer_dementia_care", used 7
    times in COMBINED_MAPPINGS for the shared offers sub-object) nest correctly
    via _set_nested below -- confirmed directly against this exact function,
    not assumed from the dotted-path convention alone.
    """
    facility = {}
    attributes = {}

    for m in table_mappings:
        raw_value = row_dict.get(m["source_column"])
        transform = TRANSFORMS[m["transform_fn"]]
        try:
            value = transform(raw_value)
        except Exception as e:
            if m["is_required"]:
                raise _RowRejected(
                    f"required field '{m['target_field']}' failed transform "
                    f"'{m['transform_fn']}' on {raw_value!r}: {e}"
                )
            log_warn(f"{source_table} | field '{m['target_field']}' nulled | "
                     f"transform '{m['transform_fn']}' failed on {raw_value!r}: {e}")
            value = None
        else:
            if m["is_required"] and value is None:
                raise _RowRejected(f"required field '{m['target_field']}' is blank")

        if m["target_layer"] == "facilities":
            facility[m["target_field"]] = value
        else:
            _set_nested(attributes, m["target_field"], value)

    return facility, attributes


def _facilities_params(facility_id, facility_type, facility, source_table, row_hash):
    return (facility_id, facility_type, *[facility.get(c) for c in FACILITIES_COLUMNS], source_table, row_hash)


def _detail_params(facility_id, attributes):
    return (facility_id, json.dumps(attributes, default=str))


async def _upsert_combined_batch(conn, source_table: str, batch: list, counters: dict):
    """
    batch: list of (facility_id, facility_type, facility, attributes, row_hash).
    Tries the whole batch as one pipelined executemany(); on any failure, falls
    back to per-row execution for just this batch so one bad row can't take
    out the rest -- same discipline as the original per-table engine, adapted
    only because a batch here can mix facility_types (a single table now holds
    all 15 active types), so facility_type travels per-row inside the batch
    tuple instead of as one shared parameter for the whole batch.
    """
    facilities_args = [_facilities_params(fid, ft, f, source_table, h) for fid, ft, f, a, h in batch]
    detail_args = [_detail_params(fid, a) for fid, ft, f, a, h in batch]
    try:
        async with conn.transaction():
            await conn.executemany(_FACILITIES_UPSERT_SQL, facilities_args)
            await conn.executemany(_DETAIL_UPSERT_SQL, detail_args)
        counters["upserted"] += len(batch)
        return
    except Exception as e:
        log_warn(f"{source_table} | batch of {len(batch)} failed, falling back to per-row | "
                  f"{type(e).__name__}: {e}")

    for fid, ft, f, a, h in batch:
        try:
            async with conn.transaction():
                await conn.execute(_FACILITIES_UPSERT_SQL, *_facilities_params(fid, ft, f, source_table, h))
                await conn.execute(_DETAIL_UPSERT_SQL, *_detail_params(fid, a))
            counters["upserted"] += 1
        except Exception as e2:
            log_error(f"{source_table} | REJECTED row (unexpected DB error) | facility_id={fid} | "
                      f"{type(e2).__name__}: {e2}")
            counters["rejected"] += 1


async def _process_combined_table(conn, source_table: str, counters: dict):
    """
    Same 4-pass shape as the original single-type engine (pure-Python
    transform+validate -> one prefetch query -> in-memory partition -> batched
    upserts with progress logging), with one insertion point in Pass 1:
    facility_type is resolved per ROW from facility_type_category before
    anything else, since this table holds all 15 active types in one place
    rather than one fixed type per whole table.
    """
    mappings_rows = await conn.fetch(
        "SELECT source_column, target_field, target_layer, transform_fn, is_required "
        "FROM infomary_source_field_mappings WHERE source_table = $1",
        source_table,
    )
    if not mappings_rows:
        log_warn(f"No mappings found for {source_table} — skipping")
        return

    table_mappings = [dict(m) for m in mappings_rows]
    rows = await conn.fetch(f'SELECT * FROM "{source_table}"')
    log_db(f"{source_table}: {len(rows)} rows (combined, multi-type)")

    # Pass 1 -- pure Python: resolve type, transform, compute identity,
    # schema-validate every row, no DB access. Dict keyed by facility_id so a
    # duplicate id within this source table resolves to "last occurrence
    # wins" instead of crashing a batched multi-row statement.
    candidates: dict[uuid.UUID, tuple[str, dict, dict, str]] = {}
    duplicate_count = 0
    for row in rows:
        row_dict = dict(row)

        category = row_dict.get("facility_type_category")
        facility_type = FACILITY_TYPE_CATEGORY_RESOLUTION.get(category)
        if facility_type is None:
            counters["excluded"] += 1
            if category not in KNOWN_CATEGORIES:
                log_error(f"{source_table} | UNRECOGNIZED facility_type_category {category!r} "
                          f"-- not a known exclusion, needs review")
                counters["unrecognized_category"] += 1
            continue

        try:
            facility, attributes = _build_record(row_dict, table_mappings, source_table)
        except _RowRejected as e:
            log_error(f"{source_table} | REJECTED row | {e}")
            counters["rejected"] += 1
            continue

        # COMBINED_MAPPINGS maps nh_*/hh_* fields unconditionally for every
        # row regardless of resolved type (to_int/to_text/etc already null
        # them out for a row where the source column is blank) -- but
        # additionalProperties: False rejects a key's mere PRESENCE even when
        # its value is null, so every non-nursing_home row would otherwise
        # carry total_certified_beds=None etc. and fail _NURSING_HOME-only
        # validation, and vice versa for hh-only keys on non-home_health
        # rows. Strip whichever set doesn't belong to this row's resolved
        # type before validation, keeping each type's schema strict/accurate
        # rather than loosening every schema to tolerate irrelevant noise.
        if facility_type != "nursing_home":
            for key in _NURSING_HOME_ONLY_KEYS:
                attributes.pop(key, None)
        if facility_type != "home_health":
            for key in _HOME_HEALTH_ONLY_KEYS:
                attributes.pop(key, None)

        # Phase 11 identity: uuid passthrough, not a computed hash -- the
        # combined source carries its own stable uuid column.
        raw_uuid = row_dict.get("uuid")
        try:
            facility_id = uuid.UUID(str(raw_uuid))
        except (ValueError, TypeError, AttributeError):
            log_error(f"{source_table} | REJECTED row | malformed or missing uuid: {raw_uuid!r}")
            counters["rejected"] += 1
            continue

        schema = SCHEMAS.get(facility_type)
        if schema:
            try:
                validate(instance=attributes, schema=schema)
            except ValidationError as e:
                log_error(f"{source_table} | REJECTED row | facility_id={facility_id} "
                          f"facility_type={facility_type} | schema validation failed: {e.message}")
                counters["rejected"] += 1
                continue

        row_hash = _canonical_hash(facility_type, facility, attributes)
        if facility_id in candidates:
            duplicate_count += 1
            log_warn(f"{source_table} | duplicate facility_id within this table: {facility_id} "
                      f"— keeping the last occurrence")
        candidates[facility_id] = (facility_type, facility, attributes, row_hash)

    if duplicate_count:
        log_warn(f"{source_table} | {duplicate_count} duplicate facility_id(s) found within this table")

    # Pass 2 -- ONE query to learn what already exists for exactly these facility_ids.
    facility_ids = list(candidates.keys())
    existing = {}
    if facility_ids:
        existing_rows = await conn.fetch(
            "SELECT facility_id, facility_type, source_row_hash FROM infomary_facilities "
            "WHERE facility_id = ANY($1::uuid[])",
            facility_ids,
        )
        existing = {r["facility_id"]: (r["facility_type"], r["source_row_hash"]) for r in existing_rows}

    # Pass 3 -- partition using the prefetched map, in memory, no queries.
    to_upsert = []
    for facility_id, (facility_type, facility, attributes, row_hash) in candidates.items():
        prior = existing.get(facility_id)
        if prior and prior[0] != facility_type:
            log_error(f"{source_table} | COLLISION | facility_id={facility_id} already exists as "
                      f"facility_type={prior[0]}, this row is {facility_type} — skipped")
            counters["collisions"] += 1
            continue
        if prior and prior[1] == row_hash:
            counters["unchanged"] += 1
            continue
        to_upsert.append((facility_id, facility_type, facility, attributes, row_hash))

    log_db(f"{source_table}: {len(to_upsert)} to upsert | {counters['unchanged']} unchanged so far | "
           f"{counters['collisions']} collisions so far | {counters['rejected']} rejected so far | "
           f"{counters['excluded']} excluded so far")

    # Pass 4 -- batched upserts with progress logging every batch.
    total_batches = max((len(to_upsert) + BATCH_SIZE - 1) // BATCH_SIZE, 1) if to_upsert else 0
    t_start = time.time()
    for batch_num, i in enumerate(range(0, len(to_upsert), BATCH_SIZE), start=1):
        batch = to_upsert[i:i + BATCH_SIZE]
        await _upsert_combined_batch(conn, source_table, batch, counters)
        elapsed = time.time() - t_start
        log_db(f"{source_table} | batch {batch_num}/{total_batches} ({len(batch)} rows) | "
               f"cumulative upserted={counters['upserted']} rejected={counters['rejected']} | "
               f"{elapsed:.1f}s elapsed")


async def _refresh_known_values(conn):
    for field in ("state", "city"):
        await conn.execute(f"""
            INSERT INTO infomary_known_values (field, value)
            SELECT DISTINCT $1, {field} FROM infomary_facilities
            WHERE {field} IS NOT NULL AND {field} != ''
            ON CONFLICT (field, value) DO NOTHING
        """, field)
    log_success("known_values refreshed from facilities")


async def run():
    counters = {
        "upserted": 0, "unchanged": 0, "rejected": 0, "collisions": 0,
        "excluded": 0, "unrecognized_category": 0,
    }
    # Separate connections, not one held across both -- Pass 1 of
    # _process_combined_table is ~60k+ rows of mostly pure-Python transform
    # work with long stretches of no DB traffic on the held connection; a
    # live run hit exactly this (asyncpg.exceptions.ConnectionDoesNotExistError
    # -- "connection was closed in the middle of operation") when
    # _refresh_known_values tried to reuse that same connection immediately
    # afterward. Acquiring fresh here means a dropped connection from the
    # long first phase can't take out the second, independent step with it.
    async with get_db_connection() as conn:
        await _process_combined_table(conn, COMBINED_TABLE, counters)
    async with get_db_connection() as conn:
        await _refresh_known_values(conn)
    log_success(
        f"ETL complete | upserted={counters['upserted']} unchanged={counters['unchanged']} "
        f"rejected={counters['rejected']} collisions={counters['collisions']} "
        f"excluded={counters['excluded']} unrecognized_category={counters['unrecognized_category']}"
    )
    return counters
