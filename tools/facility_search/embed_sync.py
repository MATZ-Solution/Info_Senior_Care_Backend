"""
Phase 3 sync engine -- diffs infomary_facilities/facility_detail against
infomary_facility_embeddings.content_hash and pushes only what changed to
Qdrant, mirroring etl.py's unchanged-short-circuit and per-batch progress
logging. Retry/backoff for the two external calls (Fireworks embed, Qdrant
upsert) lives in retry.py and is applied inside embeddings.py/qdrant_index.py
themselves -- this module only decides what to retry (whole batches) and what
to do once retries are exhausted (skip the batch, log it, keep going).
"""
import hashlib
import json
import time

from database import get_db_connection
from logger import log_db, log_success, log_warn, log_error
from tools.facility_search.content_templates import build_content
from tools.facility_search.embeddings import embed_texts, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, BATCH_SIZE
from tools.facility_search.qdrant_index import build_point, upsert_points

_TRACKING_UPSERT_SQL = """
    INSERT INTO infomary_facility_embeddings (facility_id, content_hash, embedding_model, vector_dimensions, embedded_at)
    VALUES ($1, $2, $3, $4, NOW())
    ON CONFLICT (facility_id) DO UPDATE SET
        content_hash = EXCLUDED.content_hash,
        embedding_model = EXCLUDED.embedding_model,
        vector_dimensions = EXCLUDED.vector_dimensions,
        embedded_at = NOW()
"""
# The explicit "embedded_at = NOW()" above is required, not decorative -- a
# bare column DEFAULT NOW() (see infomary_facility_embeddings in schema.py)
# only fires on INSERT, never on UPDATE. Without this, a re-embedded facility's
# tracking row would silently keep its original timestamp forever, which
# defeats the one piece of data that answers "when did this actually last change."


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _load_candidates(conn):
    return await conn.fetch("""
        SELECT f.facility_id, f.facility_type, f.city, f.state, f.ownership_type,
               f.certification_date, d.attributes, e.content_hash AS prior_hash
        FROM infomary_facilities f
        JOIN infomary_facility_detail d ON d.facility_id = f.facility_id
        LEFT JOIN infomary_facility_embeddings e ON e.facility_id = f.facility_id
    """)


async def run():
    counters = {"embedded": 0, "unchanged": 0, "errors": 0}
    async with get_db_connection() as conn:
        rows = await _load_candidates(conn)
        log_db(f"facility_embeddings: {len(rows)} facilities loaded")

        # Pass 1 -- pure Python: build content + hash, decide what changed. An
        # unrecognized facility_type (relevant once the 50+ table roadmap adds
        # new types) is isolated to just that one facility, not the whole run.
        to_embed = []  # (facility_id, facility_type, facility_dict, content, content_hash)
        for row in rows:
            facility_type = row["facility_type"]
            facility = {
                "city": row["city"],
                "state": row["state"],
                "ownership_type": row["ownership_type"],
                "certification_date": row["certification_date"],
            }
            attributes = json.loads(row["attributes"]) if row["attributes"] else {}

            try:
                content = build_content(facility_type, facility, attributes)
            except ValueError as e:
                log_error(f"facility_id={row['facility_id']} | SKIPPED (unrecognized facility_type) | {e}")
                counters["errors"] += 1
                continue

            content_hash = _content_hash(content)
            if row["prior_hash"] == content_hash:
                counters["unchanged"] += 1
                continue
            to_embed.append((row["facility_id"], facility_type, facility, content, content_hash))

        log_db(f"facility_embeddings: {len(to_embed)} to embed | {counters['unchanged']} unchanged so far")

        # Pass 2 -- sequential batches (deliberate, not left to defaults: safer
        # against Fireworks/Qdrant rate limits than unbounded concurrency for a
        # first run; revisit only if sequential throughput proves too slow).
        total_batches = max((len(to_embed) + BATCH_SIZE - 1) // BATCH_SIZE, 1) if to_embed else 0
        t_start = time.time()
        for batch_num, i in enumerate(range(0, len(to_embed), BATCH_SIZE), start=1):
            batch = to_embed[i:i + BATCH_SIZE]
            facility_ids = [b[0] for b in batch]
            texts = [b[3] for b in batch]

            try:
                vectors = await embed_texts(texts)
            except Exception as e:
                log_error(f"batch {batch_num}/{total_batches} | embedding FAILED after retries, "
                          f"skipping batch | facility_ids={facility_ids} | {type(e).__name__}: {e}")
                counters["errors"] += len(batch)
                continue

            points = [
                build_point(fid, vec, facility, facility_type)
                for (fid, facility_type, facility, _content, _content_hash), vec in zip(batch, vectors)
            ]
            try:
                await upsert_points(points)
            except Exception as e:
                log_error(f"batch {batch_num}/{total_batches} | Qdrant upsert FAILED after retries, "
                          f"skipping batch (embedded but NOT indexed -- will retry next run since "
                          f"the tracking row is not updated) | facility_ids={facility_ids} | "
                          f"{type(e).__name__}: {e}")
                counters["errors"] += len(batch)
                continue

            async with conn.transaction():
                await conn.executemany(
                    _TRACKING_UPSERT_SQL,
                    [(fid, chash, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)
                     for fid, _ft, _f, _content, chash in batch],
                )
            counters["embedded"] += len(batch)

            elapsed = time.time() - t_start
            log_db(f"batch {batch_num}/{total_batches} ({len(batch)} facilities) | "
                   f"cumulative embedded={counters['embedded']} errors={counters['errors']} | "
                   f"{elapsed:.1f}s elapsed")

    log_success(
        f"Embedding sync complete | embedded={counters['embedded']} "
        f"unchanged={counters['unchanged']} errors={counters['errors']}"
    )
    return counters
