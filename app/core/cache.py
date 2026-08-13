# """
# Cache client -- talks to Valkey over the Redis wire protocol (Valkey is a
# Redis fork and is fully compatible with redis-py, so we simply point the
# standard redis-py async client at the Valkey host/port).

# Used for:
#   - Caching read-heavy facility search/detail responses
#   - Rate limiting (via slowapi, configured separately in middlewares/rate_limit.py)
# """
# import json
# import logging
# from typing import Any, Optional

# from redis import asyncio as redis_asyncio

# from app.core.config import settings

# logger = logging.getLogger("app.cache")

# _client: Optional[redis_asyncio.Redis] = None


# def get_cache_client() -> redis_asyncio.Redis:
#     global _client
#     if _client is None:
#         _client = redis_asyncio.from_url(
#             settings.VALKEY_URL,
#             encoding="utf-8",
#             decode_responses=True,
#             socket_connect_timeout=2,
#             socket_timeout=2,
#         )
#     return _client


# async def close_cache_client() -> None:
#     global _client
#     if _client is not None:
#         await _client.aclose()
#         _client = None


# async def cache_get(key: str) -> Optional[Any]:
#     """
#     Returns deserialized JSON value, or None on miss OR on any cache error.
#     IMPORTANT: cache failures must never break the request -- always
#     degrade gracefully to a DB read.
#     """
#     try:
#         client = get_cache_client()
#         raw = await client.get(key)
#         if raw is None:
#             return None
#         return json.loads(raw)
#     except Exception as exc:
#         logger.warning("Cache GET failed for key=%s: %s", key, exc)
#         return None


# async def cache_set(key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
#     try:
#         client = get_cache_client()
#         ttl = ttl_seconds if ttl_seconds is not None else settings.CACHE_TTL_SECONDS
#         await client.set(key, json.dumps(value, default=str), ex=ttl)
#     except Exception as exc:
#         logger.warning("Cache SET failed for key=%s: %s", key, exc)


# async def cache_delete_prefix(prefix: str) -> None:
#     """Invalidate all keys under a prefix (used after writes that affect cached reads)."""
#     try:
#         client = get_cache_client()
#         async for key in client.scan_iter(match=f"{prefix}*"):
#             await client.delete(key)
#     except Exception as exc:
#         logger.warning("Cache prefix delete failed for prefix=%s: %s", prefix, exc)


# async def check_cache_connection() -> bool:
#     try:
#         client = get_cache_client()
#         return await client.ping()
#     except Exception as exc:
#         logger.error("Cache health check failed: %s", exc)
#         return False














"""
Cache client -- talks to Valkey over the Redis wire protocol (Valkey is a
Redis fork and is fully compatible with redis-py, so we simply point the
standard redis-py async client at the Valkey host/port).

Used for:
  - Caching read-heavy facility search/detail responses
  - Rate limiting (via slowapi, configured separately in middlewares/rate_limit.py)

Reliability model: the cache is an OPTIONAL speed layer, never a hard
dependency. Two safeguards keep a down/slow cache from slowing the whole app:

  1. Short socket timeouts (0.3s). A local/same-network cache that can't answer
     in 300ms is useless anyway -- waiting 2s per call just to fail turns a
     fast search into a multi-second one.
  2. A circuit breaker. Once a call fails, we mark the cache "open" (down) for
     CIRCUIT_COOLDOWN seconds and every cache_get/cache_set returns instantly
     without touching the network -- no per-request retry storm. After the
     cooldown we probe once; if it works, normal service resumes.
"""
import json
import logging
import time
from typing import Any, Optional

from redis import asyncio as redis_asyncio

from app.core.config import settings

logger = logging.getLogger("app.cache")

_client: Optional[redis_asyncio.Redis] = None

# ---- Circuit breaker state ----
# When the cache errors out, skip it entirely for this many seconds so we don't
# pay a connect timeout on every single request while it's down.
CIRCUIT_COOLDOWN = 30.0
_circuit_open_until = 0.0


def _circuit_is_open() -> bool:
    """True = cache is currently considered down; skip it, don't wait on it."""
    return time.monotonic() < _circuit_open_until


def _trip_circuit(exc: Exception) -> None:
    global _circuit_open_until
    # Only log the transition into "down", not every skipped call, to avoid
    # flooding the logs while the cache stays down.
    if not _circuit_is_open():
        logger.warning(
            "Cache unavailable -- bypassing cache for %.0fs (%s)", CIRCUIT_COOLDOWN, exc
        )
    _circuit_open_until = time.monotonic() + CIRCUIT_COOLDOWN


def _reset_circuit() -> None:
    global _circuit_open_until
    if _circuit_open_until:
        logger.info("Cache recovered -- resuming normal caching")
    _circuit_open_until = 0.0


def get_cache_client() -> redis_asyncio.Redis:
    global _client
    if _client is None:
        _client = redis_asyncio.from_url(
            settings.VALKEY_URL,
            encoding="utf-8",
            decode_responses=True,
            # Fast timeouts: a cache that can't respond in 300ms is not helping.
            socket_connect_timeout=0.3,
            socket_timeout=0.3,
        )
    return _client


async def close_cache_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


async def cache_get(key: str) -> Optional[Any]:
    """
    Returns deserialized JSON value, or None on miss OR on any cache error.
    Cache failures must never break OR slow the request -- degrade gracefully
    to a DB read. While the circuit is open, returns instantly (no network).
    """
    if _circuit_is_open():
        return None
    try:
        client = get_cache_client()
        raw = await client.get(key)
        _reset_circuit()  # a successful call clears any prior "down" state
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        _trip_circuit(exc)
        return None


async def cache_set(key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
    if _circuit_is_open():
        return
    try:
        client = get_cache_client()
        ttl = ttl_seconds if ttl_seconds is not None else settings.CACHE_TTL_SECONDS
        await client.set(key, json.dumps(value, default=str), ex=ttl)
        _reset_circuit()
    except Exception as exc:
        _trip_circuit(exc)


async def cache_delete_prefix(prefix: str) -> None:
    """Invalidate all keys under a prefix (used after writes that affect cached reads)."""
    if _circuit_is_open():
        return
    try:
        client = get_cache_client()
        async for key in client.scan_iter(match=f"{prefix}*"):
            await client.delete(key)
        _reset_circuit()
    except Exception as exc:
        _trip_circuit(exc)


async def check_cache_connection() -> bool:
    try:
        client = get_cache_client()
        ok = await client.ping()
        if ok:
            _reset_circuit()
        return ok
    except Exception as exc:
        logger.error("Cache health check failed: %s", exc)
        return False