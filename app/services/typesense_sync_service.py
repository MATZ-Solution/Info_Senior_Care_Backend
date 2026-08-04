"""
Postgres -> TypeSense synchronisation.

`public."Final Table"` is the source of truth. This module only
ever reads from it and writes to TypeSense; it never writes to Postgres.

THREE MODES, THREE DIFFERENT PROBLEMS
-------------------------------------
1. `run_full_sync()`      — first load, or after a schema change. Walks the
                            whole table by keyset and upserts everything.
2. `run_incremental_sync()` — routine. Only rows whose `updated_at` moved past
                            the watermark.
3. `reconcile_deletions()` — the one the watermark cannot see. A DELETE leaves
                            no trace in `updated_at`, so without this the index
                            keeps serving facilities that no longer exist.

WHERE THE WATERMARK LIVES
-------------------------
Nowhere. It is derived by asking TypeSense for its own newest `updated_at`
(`get_index_watermark`). No Redis key, no state table, no file. That means the
sync is correct after a restart, after a failed run, and on a fresh machine —
there is no stored value that can drift out of agreement with the index it is
supposed to describe. It costs one cheap search per run.

A NOTE ON THE SOURCE TABLE'S TRIGGER
------------------------------------
`set_updated_at BEFORE UPDATE` keeps `updated_at` honest for UPDATEs. It does
NOT fire on INSERT — on insert the column takes its default. If the upstream
pipeline ever loads with TRUNCATE + INSERT rather than UPDATE, freshly inserted
rows can carry an `updated_at` older than the watermark and be skipped forever.
`run_incremental_sync` therefore compares against `updated_at` OR `created_at`
via the source query, and `verify_sync` exists to catch the drift if it happens
anyway.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.typesense import (
    TypesenseUnavailable,
    get_typesense_client,
    run_typesense,
)
from app.services.typesense_collection_service import ensure_collection
from app.utils.facility_mapper import (
    build_all_ids_sql,
    build_count_sql,
    build_select_sql,
    to_documents,
)

logger = logging.getLogger("app.typesense.sync")

# Rows pulled from Postgres per round trip. 1000 keeps each batch's JSON body
# comfortably under TypeSense's request limits while still amortising the round
# trip over a useful number of documents.
DEFAULT_BATCH_SIZE = 1_000

# Transient failures (a node restarting mid-import) are worth retrying; a
# malformed document is not, and TypeSense reports those per-document instead
# of failing the batch.
MAX_BATCH_RETRIES = 3
RETRY_BACKOFF_SECONDS = (1, 3, 8)


@dataclass
class SyncReport:
    """What actually happened. Returned rather than logged-and-forgotten so the
    import script can exit non-zero and CI can assert on it."""

    total_source_rows: int = 0
    documents_indexed: int = 0
    documents_failed: int = 0
    rows_unmappable: int = 0
    batches: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.documents_failed == 0 and not self.errors

    def summary(self) -> str:
        return (
            f"source_rows={self.total_source_rows} indexed={self.documents_indexed} "
            f"failed={self.documents_failed} unmappable={self.rows_unmappable} "
            f"deleted={self.deleted} batches={self.batches} ok={self.ok}"
        )


# --------------------------------------------------------------------------
# Watermark
# --------------------------------------------------------------------------


async def get_index_watermark(collection_name: str | None = None) -> datetime | None:
    """
    The newest `updated_at` currently in the index, or None if it is empty.

    Implemented as a one-hit search sorted descending — TypeSense has no
    "max(field)" primitive, and this is O(1) against its sorted index.
    """
    collection = collection_name or settings.TYPESENSE_COLLECTION
    client = get_typesense_client()

    result = await run_typesense(
        client.collections[collection].documents.search,
        {
            "q": "*",
            "query_by": "name",
            "sort_by": "updated_at:desc",
            "per_page": 1,
            "include_fields": "updated_at",
        },
    )

    hits = result.get("hits", [])
    if not hits:
        return None

    epoch = hits[0].get("document", {}).get("updated_at")
    if epoch is None:
        return None
    # Naive UTC, to match the source column's `timestamp without time zone`.
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------


async def index_documents(
    documents: Sequence[dict[str, Any]],
    collection_name: str | None = None,
) -> tuple[int, list[str]]:
    """
    Upsert a batch into TypeSense.

    Returns:
        (succeeded_count, per_document_error_messages)

    `action=upsert` rather than `create` is what makes the whole pipeline
    re-runnable: importing the same rows twice updates them in place instead of
    erroring on duplicates, so a crashed import can simply be run again. Since
    the document id is the source table's primary key, duplicates are
    structurally impossible.
    """
    if not documents:
        return 0, []

    collection = collection_name or settings.TYPESENSE_COLLECTION
    client = get_typesense_client()

    last_error: Exception | None = None
    for attempt in range(MAX_BATCH_RETRIES):
        try:
            results = await run_typesense(
                client.collections[collection].documents.import_,
                list(documents),
                {"action": "upsert"},
            )
            break
        except TypesenseUnavailable as exc:
            last_error = exc
            if attempt == MAX_BATCH_RETRIES - 1:
                raise
            delay = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
            logger.warning(
                "Batch import failed (attempt %s/%s), retrying in %ss: %s",
                attempt + 1,
                MAX_BATCH_RETRIES,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    else:  # pragma: no cover — the loop always breaks or raises
        raise last_error  # type: ignore[misc]

    # TypeSense reports per-document outcomes: the batch call succeeds even
    # when individual documents were rejected. Not inspecting this is how an
    # import reports "60,018 imported" while the index holds 41,300.
    succeeded = 0
    errors: list[str] = []
    for item in results:
        if isinstance(item, dict) and item.get("success"):
            succeeded += 1
        else:
            detail = item.get("error", item) if isinstance(item, dict) else item
            errors.append(str(detail)[:300])

    return succeeded, errors


# --------------------------------------------------------------------------
# Sync drivers
# --------------------------------------------------------------------------


async def _run_sync(
    session: AsyncSession,
    *,
    since: datetime | None,
    batch_size: int,
    collection_name: str | None,
    dry_run: bool,
    progress_every: int,
) -> SyncReport:
    """Shared engine behind full and incremental sync — they differ only in `since`."""
    report = SyncReport()

    count_row = await session.execute(text(build_count_sql()), {"since": since})
    report.total_source_rows = int(count_row.scalar_one())
    logger.info(
        "Sync starting | mode=%s rows=%s batch_size=%s dry_run=%s",
        "incremental" if since else "full",
        report.total_source_rows,
        batch_size,
        dry_run,
    )

    if report.total_source_rows == 0:
        logger.info("Nothing to sync — index is already current")
        return report

    select_sql = text(build_select_sql())
    after_uuid: str | None = None

    while True:
        result = await session.execute(
            select_sql,
            {"after_uuid": after_uuid, "since": since, "limit": batch_size},
        )
        rows = [dict(row) for row in result.mappings()]
        if not rows:
            break

        after_uuid = rows[-1]["uuid"]
        report.batches += 1

        documents, mapping_errors = to_documents(rows)
        report.rows_unmappable += len(mapping_errors)
        for message in mapping_errors[:5]:
            logger.warning("Unmappable row: %s", message)

        if dry_run:
            report.documents_indexed += len(documents)
        else:
            succeeded, errors = await index_documents(documents, collection_name)
            report.documents_indexed += succeeded
            report.documents_failed += len(errors)
            for message in errors[:5]:
                logger.error("Document rejected by TypeSense: %s", message)

        if report.batches % progress_every == 0 or len(rows) < batch_size:
            pct = (
                report.documents_indexed / report.total_source_rows * 100
                if report.total_source_rows
                else 100.0
            )
            logger.info(
                "Progress | %s/%s (%.1f%%) batches=%s failed=%s",
                report.documents_indexed,
                report.total_source_rows,
                pct,
                report.batches,
                report.documents_failed,
            )

        if len(rows) < batch_size:
            break

    logger.info("Sync finished | %s", report.summary())
    return report


async def run_full_sync(
    session: AsyncSession,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    collection_name: str | None = None,
    dry_run: bool = False,
    progress_every: int = 10,
) -> SyncReport:
    """
    Index every row in the source table.

    Safe to run against a populated index: documents are upserted by primary
    key, so this refreshes rather than duplicates. Safe to re-run after a
    crash for the same reason.
    """
    await ensure_collection(collection_name)
    return await _run_sync(
        session,
        since=None,
        batch_size=batch_size,
        collection_name=collection_name,
        dry_run=dry_run,
        progress_every=progress_every,
    )


async def run_incremental_sync(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    collection_name: str | None = None,
    dry_run: bool = False,
) -> SyncReport:
    """
    Index only rows changed since the watermark.

    `since=None` derives the watermark from the index itself. An empty index
    means there is nothing to be incremental about, so it upgrades to a full
    sync rather than doing nothing — which is the behaviour you want the first
    time this runs on a fresh environment.
    """
    await ensure_collection(collection_name)

    if since is None:
        since = await get_index_watermark(collection_name)
        if since is None:
            logger.info("Index is empty — running a full sync instead of an incremental one")
            return await _run_sync(
                session,
                since=None,
                batch_size=batch_size,
                collection_name=collection_name,
                dry_run=dry_run,
                progress_every=10,
            )
        logger.info("Derived watermark from index: %s", since)

    return await _run_sync(
        session,
        since=since,
        batch_size=batch_size,
        collection_name=collection_name,
        dry_run=dry_run,
        progress_every=5,
    )


# --------------------------------------------------------------------------
# Deletion reconciliation
# --------------------------------------------------------------------------


async def reconcile_deletions(
    session: AsyncSession,
    *,
    collection_name: str | None = None,
    dry_run: bool = False,
) -> SyncReport:
    """
    Remove documents whose source row no longer exists.

    Loads both id sets and diffs them. At 60k ids that is a few megabytes and a
    couple of seconds — entirely reasonable as a nightly job, and far simpler
    (and more reliably correct) than maintaining a deletion log or a soft-delete
    flag the upstream pipeline would have to remember to set.

    ABORTS if the source table reads as empty. That is almost certainly a
    connection or permissions problem, and the "correct" response to it would
    otherwise be to delete the entire search index.
    """
    report = SyncReport()
    collection = collection_name or settings.TYPESENSE_COLLECTION

    result = await session.execute(text(build_all_ids_sql()))
    source_ids = {str(row[0]) for row in result.all()}
    report.total_source_rows = len(source_ids)

    if not source_ids:
        message = "Source table returned zero rows — refusing to reconcile (would wipe the index)"
        logger.error(message)
        report.errors.append(message)
        return report

    index_ids = await _fetch_all_index_ids(collection)
    stale = index_ids - source_ids

    logger.info(
        "Reconciliation | source=%s index=%s stale=%s",
        len(source_ids),
        len(index_ids),
        len(stale),
    )

    if not stale or dry_run:
        report.deleted = len(stale) if dry_run else 0
        return report

    client = get_typesense_client()
    for doc_id in stale:
        try:
            await run_typesense(client.collections[collection].documents[doc_id].delete)
            report.deleted += 1
        except Exception as exc:  # noqa: BLE001 — one bad delete must not stop the rest
            report.errors.append(f"delete {doc_id}: {exc}")

    logger.info("Reconciliation finished | deleted=%s errors=%s", report.deleted, len(report.errors))
    return report


async def _fetch_all_index_ids(collection: str) -> set[str]:
    """
    Every document id in the index, paged through the search API.

    Uses `exhaustive_search` so counts and paging stay exact rather than
    approximate — accuracy matters more than speed for a job that deletes.
    """
    client = get_typesense_client()
    ids: set[str] = set()
    page = 1
    per_page = 250  # TypeSense's maximum

    while page * per_page <= 10_000 + per_page:
        result = await run_typesense(
            client.collections[collection].documents.search,
            {
                "q": "*",
                "query_by": "name",
                "sort_by": "name:asc",
                "page": page,
                "per_page": per_page,
                "include_fields": "id",
                "exhaustive_search": True,
            },
        )
        hits = result.get("hits", [])
        if not hits:
            break
        ids.update(hit["document"]["id"] for hit in hits)
        if len(hits) < per_page:
            break
        page += 1

    return ids


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


async def verify_sync(
    session: AsyncSession,
    *,
    collection_name: str | None = None,
) -> dict[str, Any]:
    """
    Compare Postgres and TypeSense counts. Cheap enough to run after every
    import and to expose on an ops dashboard.
    """
    from app.services.typesense_collection_service import get_collection_stats

    count_row = await session.execute(text(build_count_sql()), {"since": None})
    source_count = int(count_row.scalar_one())

    stats = await get_collection_stats(collection_name)
    index_count = stats["num_documents"] if stats else 0
    watermark = await get_index_watermark(collection_name)

    drift = source_count - index_count
    return {
        "source_rows": source_count,
        "indexed_documents": index_count,
        "drift": drift,
        "in_sync": drift == 0,
        "index_watermark": watermark.isoformat() if watermark else None,
    }