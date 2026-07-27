# #!/usr/bin/env python
# """
# TypeSense import / sync CLI.

#     # first load — index all 60k rows
#     python -m scripts.typesense_import --full

#     # routine: only rows changed since the index's own watermark
#     python -m scripts.typesense_import --incremental

#     # see what would happen, touch nothing
#     python -m scripts.typesense_import --full --dry-run

#     # schema changed: drop, recreate, reload (index is EMPTY meanwhile)
#     python -m scripts.typesense_import --full --recreate

#     # remove documents whose source row was deleted
#     python -m scripts.typesense_import --reconcile

#     # count check only
#     python -m scripts.typesense_import --verify

# Exit codes: 0 success, 1 completed with failures, 2 could not run at all.
# Non-zero on failure is deliberate — this is meant to run from cron or a CI job,
# and a sync that silently half-works is worse than one that fails loudly.
# """
# from __future__ import annotations

# import argparse
# import asyncio
# import logging
# import sys

# from app.core.config import settings
# from app.core.database import AsyncSessionLocal
# from app.core.typesense import check_typesense_connection, is_configured
# from app.services.typesense_collection_service import (
#     ensure_collection,
#     get_collection_stats,
#     recreate_collection,
# )
# from app.services.typesense_sync_service import (
#     DEFAULT_BATCH_SIZE,
#     reconcile_deletions,
#     run_full_sync,
#     run_incremental_sync,
#     verify_sync,
# )

# logger = logging.getLogger("scripts.typesense_import")

# EXIT_OK = 0
# EXIT_COMPLETED_WITH_FAILURES = 1
# EXIT_CANNOT_RUN = 2


# def _configure_logging(verbose: bool) -> None:
#     logging.basicConfig(
#         level=logging.DEBUG if verbose else logging.INFO,
#         format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
#         datefmt="%H:%M:%S",
#         stream=sys.stdout,
#     )
#     # asyncpg/SQLAlchemy chatter drowns out progress output at DEBUG.
#     logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


# def _parse_args() -> argparse.Namespace:
#     parser = argparse.ArgumentParser(
#         description="Sync public.\"All_State_Type_combined\" into TypeSense.",
#         formatter_class=argparse.RawDescriptionHelpFormatter,
#     )
#     mode = parser.add_mutually_exclusive_group(required=True)
#     mode.add_argument("--full", action="store_true", help="index every source row")
#     mode.add_argument("--incremental", action="store_true", help="index only changed rows")
#     mode.add_argument("--reconcile", action="store_true", help="delete documents whose source row is gone")
#     mode.add_argument("--verify", action="store_true", help="compare counts, change nothing")

#     parser.add_argument(
#         "--recreate",
#         action="store_true",
#         help="DROP and recreate the collection first (schema changes only; index is empty until reload finishes)",
#     )
#     parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
#     parser.add_argument("--dry-run", action="store_true", help="read and map, but write nothing")
#     parser.add_argument("--collection", default=None, help="override the target collection name")
#     parser.add_argument("-v", "--verbose", action="store_true")
#     return parser.parse_args()


# async def _preflight() -> bool:
#     """Fail fast with a readable reason rather than a stack trace 30s in."""
#     if not is_configured():
#         logger.error(
#             "TypeSense is not configured. Set TYPESENSE_HOST and TYPESENSE_API_KEY "
#             "(and make sure TYPESENSE_SEARCH_ENABLED is not false)."
#         )
#         return False

#     if not await check_typesense_connection():
#         logger.error(
#             "Cannot reach TypeSense at %s://%s:%s — check the host, the API key, and network access.",
#             settings.TYPESENSE_PROTOCOL,
#             settings.TYPESENSE_HOST,
#             settings.TYPESENSE_PORT,
#         )
#         return False

#     logger.info(
#         "TypeSense reachable | %s://%s:%s collection=%s",
#         settings.TYPESENSE_PROTOCOL,
#         settings.TYPESENSE_HOST,
#         settings.TYPESENSE_PORT,
#         settings.TYPESENSE_COLLECTION,
#     )
#     return True


# async def _confirm_destructive(collection: str) -> bool:
#     """--recreate deletes a live index. Make the operator type it out."""
#     stats = await get_collection_stats(collection)
#     existing = stats["num_documents"] if stats else 0
#     if existing == 0:
#         return True

#     print(
#         f"\n  WARNING: --recreate will DELETE collection {collection!r} "
#         f"containing {existing:,} documents.\n"
#         f"  Search will return nothing until the reload finishes.\n"
#     )
#     answer = input("  Type the collection name to continue: ").strip()
#     if answer != collection:
#         print("  Aborted.")
#         return False
#     return True


# async def main() -> int:
#     args = _parse_args()
#     _configure_logging(args.verbose)

#     if not await _preflight():
#         return EXIT_CANNOT_RUN

#     collection = args.collection or settings.TYPESENSE_COLLECTION

#     if args.recreate:
#         if not await _confirm_destructive(collection):
#             return EXIT_CANNOT_RUN
#         await recreate_collection(collection)
#     else:
#         await ensure_collection(collection)

#     # One session for the whole run. The engine uses NullPool behind pgbouncer,
#     # so this is a single pooled connection held for the duration rather than
#     # one acquired per batch.
#     async with AsyncSessionLocal() as session:
#         if args.verify:
#             result = await verify_sync(session, collection_name=collection)
#             print("\n  Verification")
#             for key, value in result.items():
#                 print(f"    {key:<20} {value}")
#             print()
#             return EXIT_OK if result["in_sync"] else EXIT_COMPLETED_WITH_FAILURES

#         if args.reconcile:
#             report = await reconcile_deletions(
#                 session, collection_name=collection, dry_run=args.dry_run
#             )
#         elif args.full:
#             report = await run_full_sync(
#                 session,
#                 batch_size=args.batch_size,
#                 collection_name=collection,
#                 dry_run=args.dry_run,
#             )
#         else:
#             report = await run_incremental_sync(
#                 session,
#                 batch_size=args.batch_size,
#                 collection_name=collection,
#                 dry_run=args.dry_run,
#             )

#     print(f"\n  {report.summary()}")
#     if report.errors:
#         print(f"  first errors: {report.errors[:3]}")
#     print()

#     if not args.dry_run:
#         stats = await get_collection_stats(collection)
#         if stats:
#             print(f"  collection {stats['name']!r} now holds {stats['num_documents']:,} documents\n")

#     return EXIT_OK if report.ok else EXIT_COMPLETED_WITH_FAILURES


# if __name__ == "__main__":
#     try:
#         sys.exit(asyncio.run(main()))
#     except KeyboardInterrupt:
#         # Interrupting mid-import is safe: everything is an idempotent upsert
#         # keyed on the source primary key, so re-running resumes correctly.
#         print("\n  Interrupted. Re-run the same command to resume — upserts are idempotent.\n")
#         sys.exit(EXIT_CANNOT_RUN)





















#!/usr/bin/env python
"""
TypeSense import / sync CLI.

    # first load — index all 60k rows
    python -m scripts.typesense_import --full

    # routine: only rows changed since the index's own watermark
    python -m scripts.typesense_import --incremental

    # see what would happen, touch nothing
    python -m scripts.typesense_import --full --dry-run

    # schema changed: drop, recreate, reload (index is EMPTY meanwhile)
    python -m scripts.typesense_import --full --recreate

    # remove documents whose source row was deleted
    python -m scripts.typesense_import --reconcile

    # count check only
    python -m scripts.typesense_import --verify

Exit codes: 0 success, 1 completed with failures, 2 could not run at all.
Non-zero on failure is deliberate — this is meant to run from cron or a CI job,
and a sync that silently half-works is worse than one that fails loudly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.typesense import (
    check_typesense_connection,
    is_configured,
    use_admin_credentials,
)
from app.services.typesense_collection_service import (
    ensure_collection,
    get_collection_stats,
    recreate_collection,
)
from app.services.typesense_sync_service import (
    DEFAULT_BATCH_SIZE,
    reconcile_deletions,
    run_full_sync,
    run_incremental_sync,
    verify_sync,
)

logger = logging.getLogger("scripts.typesense_import")

EXIT_OK = 0
EXIT_COMPLETED_WITH_FAILURES = 1
EXIT_CANNOT_RUN = 2


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # asyncpg/SQLAlchemy chatter drowns out progress output at DEBUG.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync public.\"All_State_Type_combined\" into TypeSense.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true", help="index every source row")
    mode.add_argument("--incremental", action="store_true", help="index only changed rows")
    mode.add_argument("--reconcile", action="store_true", help="delete documents whose source row is gone")
    mode.add_argument("--verify", action="store_true", help="compare counts, change nothing")

    parser.add_argument(
        "--recreate",
        action="store_true",
        help="DROP and recreate the collection first (schema changes only; index is empty until reload finishes)",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true", help="read and map, but write nothing")
    parser.add_argument("--collection", default=None, help="override the target collection name")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


async def _preflight() -> bool:
    """Fail fast with a readable reason rather than a stack trace 30s in."""
    # This process writes, so it needs the admin key. Done before any other
    # TypeSense call so the client is never built with the read-only key.
    use_admin_credentials()

    if not is_configured():
        logger.error(
            "TypeSense is not configured. Set TYPESENSE_HOST and TYPESENSE_API_KEY "
            "(and make sure TYPESENSE_SEARCH_ENABLED is not false)."
        )
        return False

    nodes = ", ".join(
        f"{n['protocol']}://{n['host']}:{n['port']}" for n in settings.typesense_nodes
    )

    if not await check_typesense_connection():
        logger.error(
            "Could not authenticate against TypeSense at %s.\n"
            "  Checked with the key from TYPESENSE_ADMIN_API_KEY (falling back to "
            "TYPESENSE_API_KEY if unset).\n"
            "  A 401 here almost always means one of:\n"
            "    - the search-only key was pasted into TYPESENSE_ADMIN_API_KEY\n"
            "    - the key has trailing whitespace or a stray quote in .env\n"
            "    - the .env line holding it failed to parse (check dotenv warnings)\n"
            "    - the key was revoked or belongs to a different cluster",
            nodes,
        )
        return False

    logger.info("TypeSense reachable | %s collection=%s", nodes, settings.TYPESENSE_COLLECTION)
    return True


async def _confirm_destructive(collection: str) -> bool:
    """--recreate deletes a live index. Make the operator type it out."""
    stats = await get_collection_stats(collection)
    existing = stats["num_documents"] if stats else 0
    if existing == 0:
        return True

    print(
        f"\n  WARNING: --recreate will DELETE collection {collection!r} "
        f"containing {existing:,} documents.\n"
        f"  Search will return nothing until the reload finishes.\n"
    )
    answer = input("  Type the collection name to continue: ").strip()
    if answer != collection:
        print("  Aborted.")
        return False
    return True


async def main() -> int:
    args = _parse_args()
    _configure_logging(args.verbose)

    if not await _preflight():
        return EXIT_CANNOT_RUN

    collection = args.collection or settings.TYPESENSE_COLLECTION

    if args.recreate:
        if not await _confirm_destructive(collection):
            return EXIT_CANNOT_RUN
        await recreate_collection(collection)
    else:
        await ensure_collection(collection)

    # One session for the whole run. The engine uses NullPool behind pgbouncer,
    # so this is a single pooled connection held for the duration rather than
    # one acquired per batch.
    async with AsyncSessionLocal() as session:
        if args.verify:
            result = await verify_sync(session, collection_name=collection)
            print("\n  Verification")
            for key, value in result.items():
                print(f"    {key:<20} {value}")
            print()
            return EXIT_OK if result["in_sync"] else EXIT_COMPLETED_WITH_FAILURES

        if args.reconcile:
            report = await reconcile_deletions(
                session, collection_name=collection, dry_run=args.dry_run
            )
        elif args.full:
            report = await run_full_sync(
                session,
                batch_size=args.batch_size,
                collection_name=collection,
                dry_run=args.dry_run,
            )
        else:
            report = await run_incremental_sync(
                session,
                batch_size=args.batch_size,
                collection_name=collection,
                dry_run=args.dry_run,
            )

    print(f"\n  {report.summary()}")
    if report.errors:
        print(f"  first errors: {report.errors[:3]}")
    print()

    if not args.dry_run:
        stats = await get_collection_stats(collection)
        if stats:
            print(f"  collection {stats['name']!r} now holds {stats['num_documents']:,} documents\n")

    return EXIT_OK if report.ok else EXIT_COMPLETED_WITH_FAILURES


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        # Interrupting mid-import is safe: everything is an idempotent upsert
        # keyed on the source primary key, so re-running resumes correctly.
        print("\n  Interrupted. Re-run the same command to resume — upserts are idempotent.\n")
        sys.exit(EXIT_CANNOT_RUN)