"""
Generic ETL engine -- for a given source table, reads its mapping rows from
infomary_source_field_mappings (never mappings.py directly), reads the raw source
table, applies transforms.py, validates facility_detail.attributes against
schemas.py, and upserts into infomary_facilities + infomary_facility_detail.

Performance design (revised after actually running this against live Supabase --
the first cut did one existing-row SELECT plus a savepoint per row, which at
observed ~0.36s/statement network latency to a hosted Postgres instance worked
out to 10+ hours for ~35k rows):

1. Prefetch existing (facility_id, facility_type, source_row_hash) for a table's
   candidate facility_ids in ONE query -- collision/unchanged/to-upsert is then a
   dict lookup per row, not a query per row.
2. Batch the actual upserts via executemany() in chunks of BATCH_SIZE. executemany
   pipelines each row as its own statement execution rather than building one
   multi-row VALUES(...) list -- this deliberately avoids Postgres's "ON CONFLICT
   DO UPDATE command cannot affect row a second time" error, which a hand-rolled
   multi-row VALUES statement WOULD hit if a batch happened to contain two rows
   with the same facility_id (a real possibility -- duplicate CCNs within a single
   source table are already deduplicated before batching, see _process_table, but
   executemany's per-statement semantics make this safe either way).
3. Batch failure does NOT reject the whole batch: pre-validation (transforms +
   JSON Schema) already ran before any row reaches batching, so a batch failing at
   the DB level is an unexpected residual case -- it falls back to per-row
   execution for just that batch, isolating the one bad row instead of losing
   every good row alongside it.
4. Per-batch progress logging (batch number, size, cumulative counters, elapsed
   time) -- there is otherwise no visibility into progress until a whole table's
   transaction commits, which is exactly what made "is this hung or just slow"
   unanswerable last time.

Identity design: facility_id is a UUID computed deterministically from a natural
key (see _compute_facility_id), not CCN itself. CCN is never format-validated --
it's a plain audit column (source_identifier) that feeds the hash as opaque bytes,
corrupted-looking or not. See _compute_facility_id for why this exists and the
NAMESPACE constant for the one rule that must never be violated.
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
    HOSPICE_TABLE, IRF_TABLE, LTCH_TABLE, HOME_HEALTH_TABLE, NURSING_HOME_TABLE,
)

# DO NOT CHANGE -- altering this value regenerates every facility_id on the next
# ETL run (uuid5 is deterministic only as long as the namespace is fixed), which
# breaks idempotency for all previously-imported data: every facility would
# silently look "new" on the next run instead of matching its existing row.
# Fixed, hardcoded, never generated at runtime, never read from env/config.
NAMESPACE = uuid.UUID("2f0e6e2a-2b7e-4c3a-9c8d-6f3a6b1e9a1d")

SOURCE_TABLES = [
    (HOSPICE_TABLE, "hospice"),
    (IRF_TABLE, "irf"),
    (LTCH_TABLE, "ltch"),
    (HOME_HEALTH_TABLE, "home_health"),
    (NURSING_HOME_TABLE, "nursing_home"),
]

FACILITIES_COLUMNS = [
    "name", "address_line1", "address_line2", "city", "state", "zip_code",
    "county", "phone", "ownership_type", "certification_date", "cms_region",
    "latitude", "longitude", "source_identifier",
]

BATCH_SIZE = 500

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


def _compute_facility_id(source_table: str, facility: dict) -> uuid.UUID:
    """
    facility_id is never CCN itself -- it's uuid5(NAMESPACE, natural_key), so the
    same real-world facility always produces the same UUID on every ETL run
    (today, or against a refreshed CMS export next quarter), which is what makes
    re-runs update existing rows instead of duplicating them. CCN is not
    format-validated here, which is deliberate -- this is also what lets a
    corrupted-looking CCN (e.g. "1.40E+96", mangled by Excel before import) still
    produce a stable ID instead of being rejected outright.

    Fallback chain, in order:
      1. CCN present (facility["source_identifier"]) -> hash the raw CCN string
         alone, WITHOUT source_table. This is deliberate, not an oversight: a
         real CCN collision across two source tables (the exact scenario the
         mislabeled "Star Ratings" table demonstrated is possible) must still
         produce the SAME uuid on both rows, or the collision guard in
         _process_table has nothing to catch. Do NOT add source_table here "for
         consistency" with branch 2 below -- that would silently defeat the
         collision guard with no test failure to reveal it.
      2. CCN blank, but name + address_line1 + state all present -> hash
         f"{source_table}:{name}:{address_line1}:{state}". This branch
         DELIBERATELY INCLUDES source_table, unlike branch 1 -- two unrelated
         facilities in different tables that happen to share name/address/state
         must NOT collide with each other.
      3. Neither available -> a random uuid4(). No idempotency guarantee for
         this specific row on a future re-run (a repeat import would create a
         second row for the same real-world facility) -- logged loudly since
         there's no way to detect or prevent this case, only flag it.
    """
    ccn = facility.get("source_identifier")
    if ccn:
        return uuid.uuid5(NAMESPACE, ccn)

    name = facility.get("name")
    address = facility.get("address_line1")
    state = facility.get("state")
    if name and address and state:
        composite = f"{source_table}:{name}:{address}:{state}"
        log_warn(f"{source_table} | facility_id derived from name/address/state fallback "
                  f"(blank CCN) -- weaker idempotency guarantee | name={name!r}")
        return uuid.uuid5(NAMESPACE, composite)

    log_warn(f"{source_table} | facility_id is a RANDOM uuid (no CCN, no name/address/state) "
              f"-- NOT idempotent across future re-runs for this row | name={name!r}")
    return uuid.uuid4()


def _build_record(row_dict: dict, table_mappings: list, source_table: str):
    """
    Pure-Python transform pass, no DB access. Raises _RowRejected if a required
    field's transform fails or ends up blank; nulls an optional field on the same
    failure and continues. Returns (facility_dict, attributes_dict).
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


async def _upsert_batch(conn, facility_type: str, source_table: str, batch: list, counters: dict):
    """
    batch: list of (facility_id, facility, attributes, row_hash). Tries the whole
    batch as one pipelined executemany(); on any failure, falls back to per-row
    execution for just this batch so one bad row can't take out the rest.
    """
    facilities_args = [_facilities_params(fid, facility_type, f, source_table, h) for fid, f, a, h in batch]
    detail_args = [_detail_params(fid, a) for fid, f, a, h in batch]
    try:
        async with conn.transaction():
            await conn.executemany(_FACILITIES_UPSERT_SQL, facilities_args)
            await conn.executemany(_DETAIL_UPSERT_SQL, detail_args)
        counters["upserted"] += len(batch)
        return
    except Exception as e:
        log_warn(f"{source_table} | batch of {len(batch)} failed, falling back to per-row | "
                  f"{type(e).__name__}: {e}")

    for fid, f, a, h in batch:
        try:
            async with conn.transaction():
                await conn.execute(_FACILITIES_UPSERT_SQL, *_facilities_params(fid, facility_type, f, source_table, h))
                await conn.execute(_DETAIL_UPSERT_SQL, *_detail_params(fid, a))
            counters["upserted"] += 1
        except Exception as e2:
            log_error(f"{source_table} | REJECTED row (unexpected DB error) | facility_id={fid} | "
                      f"{type(e2).__name__}: {e2}")
            counters["rejected"] += 1


async def _process_table(conn, source_table: str, facility_type: str, counters: dict):
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
    log_db(f"{source_table}: {len(rows)} rows, facility_type={facility_type}")

    # Pass 1 -- pure Python: transform + schema-validate every row, no DB access.
    # Dict keyed by facility_id so a duplicate natural key within this source
    # table resolves to "last occurrence wins" (same semantics the old per-row
    # upsert loop had) instead of crashing a batched multi-row statement.
    candidates: dict[uuid.UUID, tuple[dict, dict, str]] = {}
    duplicate_count = 0
    schema = SCHEMAS.get(facility_type)
    for row in rows:
        row_dict = dict(row)
        try:
            facility, attributes = _build_record(row_dict, table_mappings, source_table)
        except _RowRejected as e:
            log_error(f"{source_table} | REJECTED row | {e}")
            counters["rejected"] += 1
            continue

        facility_id = _compute_facility_id(source_table, facility)

        if schema:
            try:
                validate(instance=attributes, schema=schema)
            except ValidationError as e:
                log_error(f"{source_table} | REJECTED row | facility_id={facility_id} "
                          f"| schema validation failed: {e.message}")
                counters["rejected"] += 1
                continue

        row_hash = _canonical_hash(facility_type, facility, attributes)
        if facility_id in candidates:
            duplicate_count += 1
            log_warn(f"{source_table} | duplicate facility_id within this table: {facility_id} "
                      f"— keeping the last occurrence")
        candidates[facility_id] = (facility, attributes, row_hash)

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
    for facility_id, (facility, attributes, row_hash) in candidates.items():
        prior = existing.get(facility_id)
        if prior and prior[0] != facility_type:
            log_error(f"{source_table} | COLLISION | facility_id={facility_id} already exists as "
                      f"facility_type={prior[0]}, this row is {facility_type} — skipped")
            counters["collisions"] += 1
            continue
        if prior and prior[1] == row_hash:
            counters["unchanged"] += 1
            continue
        to_upsert.append((facility_id, facility, attributes, row_hash))

    log_db(f"{source_table}: {len(to_upsert)} to upsert | {counters['unchanged']} unchanged so far | "
           f"{counters['collisions']} collisions so far | {counters['rejected']} rejected so far")

    # Pass 4 -- batched upserts with progress logging every batch.
    total_batches = max((len(to_upsert) + BATCH_SIZE - 1) // BATCH_SIZE, 1) if to_upsert else 0
    t_start = time.time()
    for batch_num, i in enumerate(range(0, len(to_upsert), BATCH_SIZE), start=1):
        batch = to_upsert[i:i + BATCH_SIZE]
        await _upsert_batch(conn, facility_type, source_table, batch, counters)
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
    counters = {"upserted": 0, "unchanged": 0, "rejected": 0, "collisions": 0}
    async with get_db_connection() as conn:
        for source_table, facility_type in SOURCE_TABLES:
            await _process_table(conn, source_table, facility_type, counters)
        await _refresh_known_values(conn)
    log_success(
        f"ETL complete | upserted={counters['upserted']} unchanged={counters['unchanged']} "
        f"rejected={counters['rejected']} collisions={counters['collisions']}"
    )
    return counters
