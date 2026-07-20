"""
Explore Mode — semantic search over CMS healthcare facility data.

Phase 1/2: data foundation + config (Supabase schema, lookup seeds, ETL field
mappings) -- all cheap and idempotent, so they auto-provision on every backend
boot like database.py's create_tables() already does. The bulk ETL itself
(tools/facility_search/etl.py, ~35k rows) and the embedding sync
(tools/facility_search/embed_sync.py) are NOT run here -- both are deliberately
manual steps (`run_etl.py`, `run_embed_sync.py`), since they cost real time/money
and shouldn't fire on every backend restart.

Phase 4 adds the agent-facing search capability: search_facilities is
implemented in tools/facility_search/search.py and re-exported here so this
module stays the single entrypoint representing "the tool" to the rest of the
app -- tools/agent_tools.py imports it from here (`from tools.explore_mode
import search_facilities`), the same way this module itself only ever imports
from tools.facility_search submodules rather than exposing them directly.
"""
from tools.facility_search import schema, seed, mappings
from tools.facility_search.search import search_facilities
from logger import log_error


async def ensure_facility_search_ready():
    try:
        await schema.create_tables()
        await seed.seed_facility_types()
        await mappings.seed_mappings()
    except Exception as e:
        log_error(f"Explore Mode setup failed | {type(e).__name__}: {e}")
        raise
