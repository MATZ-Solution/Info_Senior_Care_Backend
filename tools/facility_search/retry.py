"""
Shared retry/backoff helper for Phase 3's external network calls (Fireworks
embeddings, Qdrant upserts). At ~700+ batches against two external services,
transient failures (timeout, 429, brief network blip) are a "when," not an
"if" -- this is deliberately generic so both embeddings.py and qdrant_index.py
can reuse the exact same policy instead of each hand-rolling their own.
"""
import asyncio

from logger import log_warn

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 1.0  # seconds; doubles each retry: 1s, 2s, 4s


async def retry_async(coro_fn, *args, label: str, attempts: int = DEFAULT_ATTEMPTS,
                       base_delay: float = DEFAULT_BASE_DELAY, **kwargs):
    """
    Calls coro_fn(*args, **kwargs), retrying up to `attempts` times with
    exponential backoff on any exception. Re-raises the last exception if all
    attempts fail -- the caller decides what "give up" means (e.g. embed_sync
    skips just the one batch and continues, it does not abort the whole run).
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            log_warn(f"{label} | attempt {attempt}/{attempts} failed "
                     f"({type(e).__name__}: {e}) | retrying in {delay:.0f}s")
            await asyncio.sleep(delay)
    raise last_exc
