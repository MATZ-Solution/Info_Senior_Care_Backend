"""
Qdrant Cloud setup and upsert for the Phase 3 `facilities` vector collection
(architecture docs, Section 3.3). facility_id is already a UUID (Phase 2's
identity redesign) so it's used directly as the Qdrant point ID -- the docs'
"derive a UUID from facility_id" gotcha no longer applies.
"""
import os

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, Filter, PayloadSchemaType, PointStruct, ScoredPoint, VectorParams

from logger import log_db, log_success
from tools.facility_search.embeddings import EMBEDDING_DIMENSIONS
from tools.facility_search.retry import retry_async

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = "facilities"

# Every field search.py filters on -- Qdrant Cloud requires an explicit
# payload index per field to filter on it at all ("Index required but not
# found," a real 400 hit while testing Phase 4 against the live collection,
# not something the write path in Phase 3 ever needed since upsert doesn't
# filter). create_payload_index is idempotent -- confirmed by calling it twice
# against the live collection and getting COMPLETED both times -- so it's safe
# to call unconditionally on every ensure_collection() run, even against an
# already-existing, already-indexed collection.
_FILTERABLE_PAYLOAD_FIELDS = ["facility_type", "state", "city", "ownership_type"]

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        if not QDRANT_URL:
            raise RuntimeError("QDRANT_URL not set")
        _client = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _client


async def ensure_collection():
    """
    Idempotent, non-destructive -- only creates the collection if missing, same
    posture as schema.create_tables(). Deliberately does NOT migrate an
    existing collection if EMBEDDING_DIMENSIONS/EMBEDDING_MODEL changes later
    (see embeddings.py's comment on those constants) -- a mismatched vector on
    the next upsert fails loudly instead of silently corrupting the collection.
    """
    client = get_qdrant_client()
    log_db(f"Checking Qdrant collection '{COLLECTION_NAME}'...")
    if await client.collection_exists(COLLECTION_NAME):
        log_db(f"Qdrant collection '{COLLECTION_NAME}' already exists")
    else:
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
        )
        log_success(f"Qdrant collection '{COLLECTION_NAME}' created (dim={EMBEDDING_DIMENSIONS})")

    # Runs every time (not just on first creation) -- idempotent, and this is
    # exactly what fixes the "index missing" case for a collection that was
    # already created before Phase 4 added filtering.
    for field in _FILTERABLE_PAYLOAD_FIELDS:
        await client.create_payload_index(
            collection_name=COLLECTION_NAME, field_name=field, field_schema=PayloadSchemaType.KEYWORD
        )
    log_db(f"Qdrant payload indexes ensured for: {', '.join(_FILTERABLE_PAYLOAD_FIELDS)}")


def build_point(facility_id, vector: list[float], facility: dict, facility_type: str) -> PointStruct:
    # Payload carries only filter/join keys, per the docs' explicit stance that
    # Qdrant's payload is a search-optimized snapshot, never the display source
    # of truth -- Phase 4 re-fetches full display data from Supabase by facility_id.
    return PointStruct(
        id=str(facility_id),
        vector=vector,
        payload={
            "facility_id": str(facility_id),
            "facility_type": facility_type,
            "state": facility.get("state"),
            "city": facility.get("city"),
            "ownership_type": facility.get("ownership_type"),
        },
    )


async def _upsert(points: list[PointStruct]):
    client = get_qdrant_client()
    await client.upsert(collection_name=COLLECTION_NAME, points=points)


async def upsert_points(points: list[PointStruct]):
    await retry_async(_upsert, points, label=f"Qdrant upsert ({len(points)} points)")


async def _search(vector: list[float], query_filter: Filter | None, limit: int) -> list[ScoredPoint]:
    client = get_qdrant_client()
    # NOTE: qdrant-client 1.18 has removed .search() entirely -- .query_points()
    # is the current API (confirmed via hasattr(AsyncQdrantClient, "search") is False).
    response = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )
    return response.points


async def search_points(vector: list[float], query_filter: Filter | None, limit: int = 5) -> list[ScoredPoint]:
    """
    Deliberately does not pass query_points's native score_threshold param --
    that would filter out individual points server-side before they're even
    returned. search.py needs the actual top score in Python regardless (to log
    it and to word a low-confidence fallback message honestly), so fetching
    unfiltered results and checking .score in application code is the
    deliberate choice here, not an oversight of the native parameter.
    """
    return await retry_async(_search, vector, query_filter, limit, label=f"Qdrant search (limit={limit})")
