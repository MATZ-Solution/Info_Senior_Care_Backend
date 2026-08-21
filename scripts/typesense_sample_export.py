#!/usr/bin/env python
"""
Category-balanced sample export for local TypeSense testing.

Pulls up to N rows PER `facility_type_category` (default 800 x 8 categories =
6,400 documents) from the source table, maps them through the exact same
`facility_mapper.to_document()` used by the real sync (scripts/typesense_import.py)
so the sample is guaranteed to match production's document shape, and writes
the result as a `.jsonl` file — one JSON document per line, which is exactly
the body format TypeSense's bulk import endpoint expects.

WHY A FILE INSTEAD OF PUSHING DIRECTLY
---------------------------------------
This is meant to run wherever Postgres is reachable (the VPS), while TypeSense
itself runs locally on your machine for search testing — the two are not on
the same network, so this script never touches a TypeSense client at all. It
only reads Postgres and writes a file. Copy that file down (scp/rsync) and
import it locally with TypeSense's own `curl` import, no Python needed there:

    curl -X POST \\
      "http://localhost:8108/collections/<collection>/documents/import?action=upsert" \\
      -H "X-TYPESENSE-API-KEY: <your-local-api-key>" \\
      --data-binary @sample_data.jsonl

(Your local collection must already exist with the right schema first — run
`ensure_collection()` / the normal `typesense_import.py --full --dry-run`
schema bootstrap, or point ensure_collection at your local host once.)

USAGE
-----
    # default: 800 per category, all 8 categories, ./sample_data.jsonl
    python -m scripts.typesense_sample_export

    # smaller sample, one category only, custom output path
    python -m scripts.typesense_sample_export \\
        --per-category 100 \\
        --categories "Hospice" \\
        --output /tmp/hospice_sample.jsonl

Exit codes: 0 success, 1 completed with unmappable/skipped rows, 2 could not
run at all (bad DB connection etc) — same convention as typesense_import.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.recommendation_weights import CareCategory
from app.utils.facility_mapper import (
    FACILITIES_TABLE,
    SOURCE_COLUMNS,
    SOURCE_TABLE,
    to_documents,
)

logger = logging.getLogger("scripts.typesense_sample_export")

EXIT_OK = 0
EXIT_COMPLETED_WITH_FAILURES = 1
EXIT_CANNOT_RUN = 2

DEFAULT_PER_CATEGORY = 800

# All 8 categories, in the exact strings `facility_type_category` actually
# holds -- sourced from CareCategory itself rather than retyped here, so this
# can never drift out of sync with the enum used everywhere else in the app.
ALL_CATEGORIES: tuple[str, ...] = tuple(c.value for c in CareCategory)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sample N rows per facility_type_category into a TypeSense-import-ready .jsonl file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--per-category",
        type=int,
        default=DEFAULT_PER_CATEGORY,
        help=f"rows to sample per category (default {DEFAULT_PER_CATEGORY})",
    )
    parser.add_argument(
        "--categories",
        type=str,
        default=None,
        help="comma-separated subset of categories (default: all 8). "
        f"Valid values: {', '.join(ALL_CATEGORIES)}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sample_data.jsonl"),
        help="output .jsonl path (default: ./sample_data.jsonl)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Postgres random seed (setseed, range -1..1 scaled internally) for a reproducible sample",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def _build_category_sql() -> str:
    """
    One category's random sample, mapped exactly like the real sync query
    (see facility_mapper.build_select_sql) -- same JOIN, same active-only
    filter, same column list -- just filtered to one category and ordered
    randomly instead of by keyset, since this is a one-shot sample rather
    than a resumable full walk.
    """
    src_columns = ", ".join(f'src."{col}"' for col in SOURCE_COLUMNS if col != "uuid")
    return (  # noqa: S608 — identifiers hardcoded, values bound
        f"SELECT fac.id::text AS uuid, {src_columns} "
        f"FROM {SOURCE_TABLE} AS src "
        f"JOIN {FACILITIES_TABLE} AS fac ON fac.source_uuid = src.\"uuid\" "
        f"WHERE fac.is_active = true "
        f'  AND src."facility_type_category" = :category '
        f"ORDER BY random() "
        f"LIMIT :limit"
    )


async def _sample_one_category(session, category: str, per_category: int) -> tuple[list[dict], list[str]]:
    result = await session.execute(
        text(_build_category_sql()),
        {"category": category, "limit": per_category},
    )
    rows = [dict(row) for row in result.mappings()]
    documents, errors = to_documents(rows)
    logger.info(
        "%-42s requested=%-5s got=%-5s mapped=%-5s unmappable=%s",
        category,
        per_category,
        len(rows),
        len(documents),
        len(errors),
    )
    for message in errors[:3]:
        logger.warning("  unmappable row (%s): %s", category, message)
    return documents, errors


async def _run(args: argparse.Namespace) -> int:
    categories = (
        tuple(c.strip() for c in args.categories.split(",") if c.strip())
        if args.categories
        else ALL_CATEGORIES
    )
    unknown = set(categories) - set(ALL_CATEGORIES)
    if unknown:
        logger.error(
            "Unknown categor%s: %s\nValid values: %s",
            "y" if len(unknown) == 1 else "ies",
            ", ".join(sorted(unknown)),
            ", ".join(ALL_CATEGORIES),
        )
        return EXIT_CANNOT_RUN

    all_documents: list[dict] = []
    total_errors = 0

    async with AsyncSessionLocal() as session:
        if args.seed is not None:
            # setseed() takes a float in [-1, 1]; scale the int seed down so
            # any integer the operator passes maps into that range.
            await session.execute(text("SELECT setseed(:s)"), {"s": (args.seed % 2000 - 1000) / 1000})

        for category in categories:
            documents, errors = await _sample_one_category(session, category, args.per_category)
            all_documents.extend(documents)
            total_errors += len(errors)

    if not all_documents:
        logger.error("Nothing sampled at all — check the DB connection and category spelling")
        return EXIT_CANNOT_RUN

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for doc in all_documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    logger.info(
        "Wrote %s documents across %s categories -> %s",
        len(all_documents),
        len(categories),
        args.output,
    )
    print(f"\n  {len(all_documents)} documents written to {args.output}")
    print("  Copy this file to your local machine and import with:\n")
    print(
        '    curl -X POST "http://localhost:8108/collections/<collection>/documents/import?action=upsert" \\\n'
        '      -H "X-TYPESENSE-API-KEY: <your-local-api-key>" \\\n'
        f"      --data-binary @{args.output.name}\n"
    )

    return EXIT_OK if total_errors == 0 else EXIT_COMPLETED_WITH_FAILURES


async def main() -> int:
    args = _parse_args()
    _configure_logging(args.verbose)
    try:
        return await _run(args)
    except Exception:
        logger.exception("Sample export failed")
        return EXIT_CANNOT_RUN


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
