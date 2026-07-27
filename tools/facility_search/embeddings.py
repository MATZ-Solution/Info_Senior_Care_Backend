"""
Fireworks AI embeddings client (Qwen3-Embedding-8B) -- a plain OpenAI-compatible
REST call over httpx, no embedding SDK needed.
https://docs.fireworks.ai/guides/querying-embeddings-models
"""
import os

import httpx

from tools.facility_search.retry import retry_async

FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
EMBEDDINGS_URL = "https://api.fireworks.ai/inference/v1/embeddings"
EMBEDDING_MODEL = "fireworks/qwen3-embedding-8b"

# Qwen3-Embedding-8B natively outputs 4096 dims; it's MRL-trained so Fireworks'
# `dimensions` request param truncates it while keeping most of its semantic
# quality. 1024 keeps Qdrant Cloud's free-tier storage/RAM footprint reasonable
# (~140MB for 35k facilities vs. ~570MB raw at 4096).
#
# Changing EMBEDDING_MODEL or EMBEDDING_DIMENSIONS requires manually dropping
# and recreating the Qdrant collection -- qdrant_index.ensure_collection() only
# creates the collection if missing, it will NOT migrate an existing one to a
# new vector size. The failure mode if you forget this is loud (Qdrant rejects
# the mismatched vector on the next upsert), not silent corruption -- but it's
# still a manual step you must remember to do.
EMBEDDING_DIMENSIONS = 1024

BATCH_SIZE = 50


async def _post(texts: list[str]) -> list[list[float]]:
    if not FIREWORKS_API_KEY:
        raise RuntimeError("FIREWORKS_API_KEY not set")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            EMBEDDINGS_URL,
            headers={"Authorization": f"Bearer {FIREWORKS_API_KEY}", "Content-Type": "application/json"},
            json={"input": texts, "model": EMBEDDING_MODEL, "dimensions": EMBEDDING_DIMENSIONS},
        )
        response.raise_for_status()
        data = response.json()["data"]
        return [item["embedding"] for item in data]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    texts: up to BATCH_SIZE strings, already chunked by the caller (embed_sync.py
    owns batching so it can keep facility_ids aligned with vectors and tracking
    rows). Retries the whole batch up to 3x with exponential backoff; raises on
    final failure so the caller can log and skip just this batch.
    """
    return await retry_async(_post, texts, label=f"Fireworks embed ({len(texts)} texts)")
