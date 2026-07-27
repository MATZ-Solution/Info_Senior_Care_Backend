"""
Standalone CLI entrypoint for Phase 3's embedding sync -- deliberately NOT
wired into main.py's lifespan, same reasoning as run_etl.py: embedding calls
cost money and take time, so this is a manual/batch step, not something to
re-run on every backend boot.

Run: uv run python -m tools.facility_search.run_embed_sync
"""
import asyncio

import database
from tools.facility_search import qdrant_index, embed_sync
from logger import log_divider


async def main():
    log_divider("EMBEDDING SYNC")
    await database.init_db_pool()
    try:
        await qdrant_index.ensure_collection()
        await embed_sync.run()
    finally:
        await database.close_db_pool()
    log_divider("DONE")


if __name__ == "__main__":
    asyncio.run(main())
