"""
Standalone CLI entrypoint for the facility ETL -- deliberately NOT wired into
main.py's lifespan. ~65k+ rows from All_State_Type_combined (Phase 11's single
active source) is a manual/batch step, not something to re-run on every
backend boot.

Run: uv run python -m tools.facility_search.run_etl
"""
import asyncio

import database
from tools.facility_search import schema, mappings, etl
from logger import log_divider


async def main():
    log_divider("FACILITY ETL")
    await database.init_db_pool()
    try:
        # Defensive: works standalone even if the app was never booted in this env.
        await schema.create_tables()
        await mappings.seed_mappings()
        await etl.run()
    finally:
        await database.close_db_pool()
    log_divider("DONE")


if __name__ == "__main__":
    asyncio.run(main())
