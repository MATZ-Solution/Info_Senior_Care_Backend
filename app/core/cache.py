"""
Cache client -- talks to Valkey over the Redis wire protocol (Valkey is a
Redis fork and is fully compatible with redis-py, so we simply point the
standard redis-py async client at the Valkey host/port).

Used for:
  - Caching read-heavy facility search/detail responses
  - Rate limiting (via slowapi, configured separately in middlewares/rate_limit.py)
"""
import json
import logging
from typing import Any, Optional

from redis import asyncio as redis_asyncio

from app.core.config import settings

logger = logging.getLogger("app.cache")

_client: Optional[redis_asyncio.Redis] = None


def get_cache_client() -> redis_asyncio.Redis:
    global _client
    if _client is None:
        _client = redis_asyncio.from_url(
            settings.VALKEY_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


async def close_cache_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def cache_get(key: str) -> Optional[Any]:
    """
    Returns deserialized JSON value, or None on miss OR on any cache error.
    IMPORTANT: cache failures must never break the request -- always
    degrade gracefully to a DB read.
    """
    try:
        client = get_cache_client()
        raw = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Cache GET failed for key=%s: %s", key, exc)
        return None


async def cache_set(key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
    try:
        client = get_cache_client()
        ttl = ttl_seconds if ttl_seconds is not None else settings.CACHE_TTL_SECONDS
        await client.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception as exc:
        logger.warning("Cache SET failed for key=%s: %s", key, exc)


async def cache_delete_prefix(prefix: str) -> None:
    """Invalidate all keys under a prefix (used after writes that affect cached reads)."""
    try:
        client = get_cache_client()
        async for key in client.scan_iter(match=f"{prefix}*"):
            await client.delete(key)
    except Exception as exc:
        logger.warning("Cache prefix delete failed for prefix=%s: %s", prefix, exc)


async def check_cache_connection() -> bool:
    try:
        client = get_cache_client()
        return await client.ping()
    except Exception as exc:
        logger.error("Cache health check failed: %s", exc)
        return False
