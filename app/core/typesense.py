# """
# TypeSense client bootstrap.

# Scope of this module: OWN THE CONNECTION, nothing else. No collection schema,
# no mapping, no query building — those live in app/services/* and
# app/utils/facility_mapper.py so each piece stays independently testable.

# Three things worth knowing before you read the code:

# 1. The official `typesense` Python client is SYNCHRONOUS (it wraps `requests`).
#    Calling it directly from an `async def` FastAPI endpoint blocks the event
#    loop for the whole round trip — under load that stalls every other request
#    on the worker. Every call therefore goes through `run_typesense()`, which
#    hands the blocking call to Starlette's threadpool.

# 2. TypeSense is a DERIVED index, never the source of truth. Postgres is. So
#    nothing here may take the application down: a missing config, an unreachable
#    node, or an expired API key must surface as a catchable
#    `TypesenseUnavailable`, letting the caller fall back to the existing
#    Postgres search path.

# 3. The client is a process-wide singleton. It holds a `requests.Session` with a
#    connection pool; building a new one per request would mean a fresh TCP + TLS
#    handshake every single search.
# """
# from __future__ import annotations

# import logging
# import threading
# from typing import Any, Callable, TypeVar

# import typesense
# from starlette.concurrency import run_in_threadpool
# from typesense.exceptions import (
#     ObjectNotFound,
#     RequestForbidden,
#     RequestUnauthorized,
#     ServiceUnavailable,
#     Timeout,
#     TypesenseClientError,
# )

# from app.core.config import settings

# logger = logging.getLogger("app.typesense")

# T = TypeVar("T")

# # Module-level singleton. `_client_lock` guards creation only — the client
# # itself is safe to share across threads (requests.Session is thread-safe for
# # our usage pattern, and the threadpool is where the calls actually run).
# _client: typesense.Client | None = None
# _client_lock = threading.Lock()


# class TypesenseUnavailable(RuntimeError):
#     """
#     TypeSense could not be used for this call.

#     Deliberately a single exception type covering "not configured",
#     "unreachable", "auth rejected" and "timed out": from the caller's point of
#     view the response is identical in all four cases — degrade to Postgres.
#     The distinction that matters for debugging goes into the log, not into the
#     control flow.
#     """


# def is_configured() -> bool:
#     """
#     Whether TypeSense credentials are present AND the feature is switched on.

#     Read this before attempting a search so the fallback is a normal branch
#     rather than an exception path — exceptions are for failures, and a
#     deliberately-disabled integration is not a failure.
#     """
#     return settings.typesense_configured and settings.TYPESENSE_SEARCH_ENABLED


# def get_typesense_client() -> typesense.Client:
#     """
#     Return the process-wide client, building it on first use.

#     Lazy rather than built at import time: an unreachable or misconfigured
#     TypeSense must not stop the application from importing and booting.

#     Raises:
#         TypesenseUnavailable: if the integration is unconfigured or disabled.
#     """
#     global _client

#     # Fast path — no lock once the client exists (the overwhelming majority of
#     # calls). Safe because assignment to a module global is atomic in CPython
#     # and we only ever assign a fully-constructed client.
#     if _client is not None:
#         return _client

#     if not is_configured():
#         raise TypesenseUnavailable(
#             "TypeSense is not configured or is disabled "
#             "(TYPESENSE_HOST / TYPESENSE_API_KEY / TYPESENSE_SEARCH_ENABLED)."
#         )

#     with _client_lock:
#         # Re-check inside the lock: two threads can both miss the fast path.
#         if _client is None:
#             logger.info(
#                 "Initializing TypeSense client | host=%s port=%s protocol=%s "
#                 "collection=%s timeout=%ss retries=%s",
#                 settings.TYPESENSE_HOST,
#                 settings.TYPESENSE_PORT,
#                 settings.TYPESENSE_PROTOCOL,
#                 settings.TYPESENSE_COLLECTION,
#                 settings.TYPESENSE_TIMEOUT_SECONDS,
#                 settings.TYPESENSE_NUM_RETRIES,
#             )
#             _client = typesense.Client(
#                 {
#                     "nodes": [
#                         {
#                             "host": settings.TYPESENSE_HOST,
#                             "port": settings.TYPESENSE_PORT,
#                             "protocol": settings.TYPESENSE_PROTOCOL,
#                         }
#                     ],
#                     "api_key": settings.TYPESENSE_API_KEY,
#                     "connection_timeout_seconds": settings.TYPESENSE_TIMEOUT_SECONDS,
#                     "num_retries": settings.TYPESENSE_NUM_RETRIES,
#                     "retry_interval_seconds": settings.TYPESENSE_RETRY_INTERVAL_SECONDS,
#                 }
#             )

#     return _client


# def reset_typesense_client() -> None:
#     """
#     Drop the cached client so the next call rebuilds it.

#     Used by tests (to swap in a fake) and by the shutdown hook. Not needed in
#     normal request handling.
#     """
#     global _client
#     with _client_lock:
#         _client = None


# async def run_typesense(operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
#     """
#     Execute a blocking TypeSense call off the event loop and normalize errors.

#     Usage:
#         client = get_typesense_client()
#         result = await run_typesense(
#             client.collections[name].documents.search, search_params
#         )

#     Raises:
#         TypesenseUnavailable: connection, auth, timeout or 5xx failures — the
#             caller should fall back to Postgres.
#         ObjectNotFound: passed through untouched. "Collection does not exist"
#             is a distinct, actionable condition (run the bootstrap/import), not
#             a transport failure, so callers get to handle it separately.
#         TypesenseClientError: other 4xx (e.g. a malformed query we built). Also
#             passed through — that is OUR bug and should be visible in Sentry,
#             not silently swallowed as a fallback.
#     """
#     try:
#         return await run_in_threadpool(operation, *args, **kwargs)

#     except ObjectNotFound:
#         raise

#     except (RequestUnauthorized, RequestForbidden) as exc:
#         # Almost always a rotated/wrong API key. Log loudly — this will not
#         # heal on its own, unlike a transient network blip.
#         logger.error("TypeSense rejected our API key: %s", exc)
#         raise TypesenseUnavailable("TypeSense authentication failed") from exc

#     except (Timeout, ServiceUnavailable) as exc:
#         logger.warning("TypeSense unavailable (timeout/503): %s", exc)
#         raise TypesenseUnavailable("TypeSense is temporarily unavailable") from exc

#     except ConnectionError as exc:
#         # requests raises this for DNS/TCP failures before TypeSense's own
#         # exception layer ever sees the response.
#         logger.warning("Could not connect to TypeSense: %s", exc)
#         raise TypesenseUnavailable("Could not connect to TypeSense") from exc

#     except TypesenseClientError:
#         # 4xx/5xx we did not classify above. Surface it — see docstring.
#         logger.exception("Unexpected TypeSense client error")
#         raise


# async def check_typesense_connection() -> bool:
#     """
#     Liveness probe for the search backend. Never raises.

#     Wired into GET /health/ready in the next step so a broken search cluster is
#     visible on the dashboard rather than discovered through user complaints.
#     """
#     if not is_configured():
#         return False
#     try:
#         client = get_typesense_client()
#         result = await run_typesense(client.operations.is_healthy)
#         return bool(result)
#     except Exception as exc:  # noqa: BLE001 — a health check must never raise
#         logger.warning("TypeSense health check failed: %s", exc)
#         return False










# """
# TypeSense client bootstrap.

# Scope of this module: OWN THE CONNECTION, nothing else. No collection schema,
# no mapping, no query building — those live in app/services/* and
# app/utils/facility_mapper.py so each piece stays independently testable.

# Three things worth knowing before you read the code:

# 1. The official `typesense` Python client is SYNCHRONOUS (it wraps `requests`).
#    Calling it directly from an `async def` FastAPI endpoint blocks the event
#    loop for the whole round trip — under load that stalls every other request
#    on the worker. Every call therefore goes through `run_typesense()`, which
#    hands the blocking call to Starlette's threadpool.

# 2. TypeSense is a DERIVED index, never the source of truth. Postgres is. So
#    nothing here may take the application down: a missing config, an unreachable
#    node, or an expired API key must surface as a catchable
#    `TypesenseUnavailable`, letting the caller fall back to the existing
#    Postgres search path.

# 3. The client is a process-wide singleton. It holds a `requests.Session` with a
#    connection pool; building a new one per request would mean a fresh TCP + TLS
#    handshake every single search.
# """
# from __future__ import annotations

# import logging
# import threading
# from typing import Any, Callable, TypeVar

# import typesense
# from starlette.concurrency import run_in_threadpool
# from typesense.exceptions import (
#     ObjectNotFound,
#     RequestForbidden,
#     RequestUnauthorized,
#     ServiceUnavailable,
#     Timeout,
#     TypesenseClientError,
# )

# from app.core.config import settings

# logger = logging.getLogger("app.typesense")

# T = TypeVar("T")

# # Module-level singleton. `_client_lock` guards creation only — the client
# # itself is safe to share across threads (requests.Session is thread-safe for
# # our usage pattern, and the threadpool is where the calls actually run).
# _client: typesense.Client | None = None
# _client_lock = threading.Lock()

# # Set only by `use_admin_credentials()`. The web process never touches it, so
# # it holds search-only credentials for its entire life.
# _api_key_override: str | None = None


# def _active_api_key() -> str:
#     """The key this process should authenticate with."""
#     return _api_key_override or settings.TYPESENSE_API_KEY


# def use_admin_credentials() -> None:
#     """
#     Switch this PROCESS to the admin key. Called once by the import CLI.

#     A process-level switch rather than an `admin=True` flag threaded through
#     every service call: the split is between long-running web workers (which
#     only ever read) and the short-lived import script (which only ever writes),
#     not between individual calls. Keeping it out of the service signatures
#     means no call site can accidentally acquire write access.

#     Falls back to TYPESENSE_API_KEY when TYPESENSE_ADMIN_API_KEY is unset, so a
#     single-key setup still works — though collection creation will then fail if
#     that key is search-only, which the warning below makes obvious.
#     """
#     global _api_key_override

#     admin_key = settings.TYPESENSE_ADMIN_API_KEY.strip()
#     if not admin_key:
#         logger.warning(
#             "TYPESENSE_ADMIN_API_KEY is not set — falling back to TYPESENSE_API_KEY. "
#             "If that is a search-only key, collection creation and document "
#             "writes will be rejected."
#         )
#         admin_key = settings.TYPESENSE_API_KEY

#     _api_key_override = admin_key
#     # Any client built before this point authenticates with the wrong key.
#     reset_typesense_client()
#     logger.info("TypeSense client switched to admin credentials")


# class TypesenseUnavailable(RuntimeError):
#     """
#     TypeSense could not be used for this call.

#     Deliberately a single exception type covering "not configured",
#     "unreachable", "auth rejected" and "timed out": from the caller's point of
#     view the response is identical in all four cases — degrade to Postgres.
#     The distinction that matters for debugging goes into the log, not into the
#     control flow.
#     """


# def is_configured() -> bool:
#     """
#     Whether TypeSense credentials are present AND the feature is switched on.

#     Read this before attempting a search so the fallback is a normal branch
#     rather than an exception path — exceptions are for failures, and a
#     deliberately-disabled integration is not a failure.
#     """
#     return bool(
#         settings.typesense_nodes
#         and _active_api_key()
#         and settings.TYPESENSE_SEARCH_ENABLED
#     )


# def get_typesense_client() -> typesense.Client:
#     """
#     Return the process-wide client, building it on first use.

#     Lazy rather than built at import time: an unreachable or misconfigured
#     TypeSense must not stop the application from importing and booting.

#     Raises:
#         TypesenseUnavailable: if the integration is unconfigured or disabled.
#     """
#     global _client

#     # Fast path — no lock once the client exists (the overwhelming majority of
#     # calls). Safe because assignment to a module global is atomic in CPython
#     # and we only ever assign a fully-constructed client.
#     if _client is not None:
#         return _client

#     if not is_configured():
#         raise TypesenseUnavailable(
#             "TypeSense is not configured or is disabled "
#             "(TYPESENSE_HOST / TYPESENSE_API_KEY / TYPESENSE_SEARCH_ENABLED)."
#         )

#     with _client_lock:
#         # Re-check inside the lock: two threads can both miss the fast path.
#         if _client is None:
#             nodes = settings.typesense_nodes
#             logger.info(
#                 "Initializing TypeSense client | nodes=%s collection=%s "
#                 "timeout=%ss retries=%s",
#                 [f"{n['protocol']}://{n['host']}:{n['port']}" for n in nodes],
#                 settings.TYPESENSE_COLLECTION,
#                 settings.TYPESENSE_TIMEOUT_SECONDS,
#                 settings.TYPESENSE_NUM_RETRIES,
#             )
#             _client = typesense.Client(
#                 {
#                     # Multiple nodes give the client automatic failover: if one
#                     # node is restarting, the request is retried against the
#                     # next rather than surfacing as a search outage.
#                     "nodes": nodes,
#                     "api_key": _active_api_key(),
#                     "connection_timeout_seconds": settings.TYPESENSE_TIMEOUT_SECONDS,
#                     "num_retries": settings.TYPESENSE_NUM_RETRIES,
#                     "retry_interval_seconds": settings.TYPESENSE_RETRY_INTERVAL_SECONDS,
#                 }
#             )

#     return _client


# def reset_typesense_client() -> None:
#     """
#     Drop the cached client so the next call rebuilds it.

#     Used by tests (to swap in a fake) and by the shutdown hook. Not needed in
#     normal request handling.
#     """
#     global _client
#     with _client_lock:
#         _client = None


# async def run_typesense(operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
#     """
#     Execute a blocking TypeSense call off the event loop and normalize errors.

#     Usage:
#         client = get_typesense_client()
#         result = await run_typesense(
#             client.collections[name].documents.search, search_params
#         )

#     Raises:
#         TypesenseUnavailable: connection, auth, timeout or 5xx failures — the
#             caller should fall back to Postgres.
#         ObjectNotFound: passed through untouched. "Collection does not exist"
#             is a distinct, actionable condition (run the bootstrap/import), not
#             a transport failure, so callers get to handle it separately.
#         TypesenseClientError: other 4xx (e.g. a malformed query we built). Also
#             passed through — that is OUR bug and should be visible in Sentry,
#             not silently swallowed as a fallback.
#     """
#     try:
#         return await run_in_threadpool(operation, *args, **kwargs)

#     except ObjectNotFound:
#         raise

#     except (RequestUnauthorized, RequestForbidden) as exc:
#         # Almost always a rotated/wrong API key. Log loudly — this will not
#         # heal on its own, unlike a transient network blip.
#         logger.error("TypeSense rejected our API key: %s", exc)
#         raise TypesenseUnavailable("TypeSense authentication failed") from exc

#     except (Timeout, ServiceUnavailable) as exc:
#         logger.warning("TypeSense unavailable (timeout/503): %s", exc)
#         raise TypesenseUnavailable("TypeSense is temporarily unavailable") from exc

#     except ConnectionError as exc:
#         # requests raises this for DNS/TCP failures before TypeSense's own
#         # exception layer ever sees the response.
#         logger.warning("Could not connect to TypeSense: %s", exc)
#         raise TypesenseUnavailable("Could not connect to TypeSense") from exc

#     except TypesenseClientError:
#         # 4xx/5xx we did not classify above. Surface it — see docstring.
#         logger.exception("Unexpected TypeSense client error")
#         raise


# async def check_typesense_connection() -> bool:
#     """
#     Verify we can actually USE TypeSense. Never raises.

#     Deliberately does NOT use `operations.is_healthy`. That maps to TypeSense's
#     `GET /health`, which is UNAUTHENTICATED — it returns true for a wrong,
#     revoked or empty API key. A preflight built on it reports "reachable" and
#     then the very next call fails with 401, which is worse than no check at
#     all because it points the operator at the network instead of the key.

#     So both strategies below make an authenticated call, and which one applies
#     depends on the key in use:

#     1. `collections.retrieve()` — lists collections. Requires an ADMIN key.
#     2. A one-hit search on the configured collection — what a SEARCH-ONLY key
#        is allowed to do, and exactly what the running app needs.

#     A search-only key is the right key for the web process (if the environment
#     leaks, it cannot delete the index). It gets a 401 from strategy 1, so
#     falling through to strategy 2 checks the permission the app actually uses
#     rather than one it never needs.
#     """
#     if not is_configured():
#         return False

#     try:
#         client = get_typesense_client()
#     except TypesenseUnavailable:
#         return False

#     try:
#         await run_typesense(client.collections.retrieve)
#         return True
#     except TypesenseUnavailable:
#         # Either an auth rejection (search-only key) or a genuinely unreachable
#         # cluster. Strategy 2 tells the two apart.
#         pass
#     except Exception as exc:  # noqa: BLE001 — a health check must never raise
#         logger.warning("TypeSense health check failed: %s", exc)
#         return False

#     try:
#         await run_typesense(
#             client.collections[settings.TYPESENSE_COLLECTION].documents.search,
#             {"q": "*", "query_by": "name", "per_page": 1, "include_fields": "id"},
#         )
#         return True
#     except ObjectNotFound:
#         # Auth is fine — the collection just has not been created yet. That is
#         # a valid state before the first import, and the caller (which creates
#         # the collection) must not be blocked by it.
#         logger.info(
#             "TypeSense authenticated, but collection %r does not exist yet",
#             settings.TYPESENSE_COLLECTION,
#         )
#         return True
#     except Exception as exc:  # noqa: BLE001 — a health check must never raise
#         logger.warning("TypeSense health check failed: %s", exc)
#         return False

























"""
TypeSense client bootstrap.

Scope of this module: OWN THE CONNECTION, nothing else. No collection schema,
no mapping, no query building — those live in app/services/* and
app/utils/facility_mapper.py so each piece stays independently testable.

Three things worth knowing before you read the code:

1. The official `typesense` Python client is SYNCHRONOUS (it wraps `requests`).
   Calling it directly from an `async def` FastAPI endpoint blocks the event
   loop for the whole round trip — under load that stalls every other request
   on the worker. Every call therefore goes through `run_typesense()`, which
   hands the blocking call to Starlette's threadpool.

2. TypeSense is a DERIVED index, never the source of truth. Postgres is. So
   nothing here may take the application down: a missing config, an unreachable
   node, or an expired API key must surface as a catchable
   `TypesenseUnavailable`, letting the caller fall back to the existing
   Postgres search path.

3. The client is a process-wide singleton. It holds a `requests.Session` with a
   connection pool; building a new one per request would mean a fresh TCP + TLS
   handshake every single search.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

import typesense
from starlette.concurrency import run_in_threadpool
from typesense.exceptions import (
    ObjectNotFound,
    RequestForbidden,
    RequestUnauthorized,
    ServiceUnavailable,
    Timeout,
    TypesenseClientError,
)

from app.core.config import settings

logger = logging.getLogger("app.typesense")

T = TypeVar("T")

# Module-level singleton. `_client_lock` guards creation only — the client
# itself is safe to share across threads (requests.Session is thread-safe for
# our usage pattern, and the threadpool is where the calls actually run).
_client: typesense.Client | None = None
_client_lock = threading.Lock()

# Set only by `use_admin_credentials()`. The web process never touches it, so
# it holds search-only credentials for its entire life.
_api_key_override: str | None = None


def _active_api_key() -> str:
    """The key this process should authenticate with."""
    return _api_key_override or settings.TYPESENSE_API_KEY


def use_admin_credentials() -> None:
    """
    Switch this PROCESS to the admin key. Called once by the import CLI.

    A process-level switch rather than an `admin=True` flag threaded through
    every service call: the split is between long-running web workers (which
    only ever read) and the short-lived import script (which only ever writes),
    not between individual calls. Keeping it out of the service signatures
    means no call site can accidentally acquire write access.

    Falls back to TYPESENSE_API_KEY when TYPESENSE_ADMIN_API_KEY is unset, so a
    single-key setup still works — though collection creation will then fail if
    that key is search-only, which the warning below makes obvious.
    """
    global _api_key_override

    admin_key = settings.TYPESENSE_ADMIN_API_KEY.strip()
    if not admin_key:
        logger.warning(
            "TYPESENSE_ADMIN_API_KEY is not set — falling back to TYPESENSE_API_KEY. "
            "If that is a search-only key, collection creation and document "
            "writes will be rejected."
        )
        admin_key = settings.TYPESENSE_API_KEY

    _api_key_override = admin_key
    # Any client built before this point authenticates with the wrong key.
    reset_typesense_client()
    logger.info("TypeSense client switched to admin credentials")


class TypesenseUnavailable(RuntimeError):
    """
    TypeSense could not be used for this call.

    Deliberately a single exception type covering "not configured",
    "unreachable", "auth rejected" and "timed out": from the caller's point of
    view the response is identical in all four cases — degrade to Postgres.
    The distinction that matters for debugging goes into the log, not into the
    control flow.
    """


def is_configured() -> bool:
    """
    Whether TypeSense credentials are present AND the feature is switched on.

    Read this before attempting a search so the fallback is a normal branch
    rather than an exception path — exceptions are for failures, and a
    deliberately-disabled integration is not a failure.
    """
    return bool(
        settings.typesense_nodes
        and _active_api_key()
        and settings.TYPESENSE_SEARCH_ENABLED
    )


def get_typesense_client() -> typesense.Client:
    """
    Return the process-wide client, building it on first use.

    Lazy rather than built at import time: an unreachable or misconfigured
    TypeSense must not stop the application from importing and booting.

    Raises:
        TypesenseUnavailable: if the integration is unconfigured or disabled.
    """
    global _client

    # Fast path — no lock once the client exists (the overwhelming majority of
    # calls). Safe because assignment to a module global is atomic in CPython
    # and we only ever assign a fully-constructed client.
    if _client is not None:
        return _client

    if not is_configured():
        raise TypesenseUnavailable(
            "TypeSense is not configured or is disabled "
            "(TYPESENSE_HOST / TYPESENSE_API_KEY / TYPESENSE_SEARCH_ENABLED)."
        )

    with _client_lock:
        # Re-check inside the lock: two threads can both miss the fast path.
        if _client is None:
            nodes = settings.typesense_nodes
            logger.info(
                "Initializing TypeSense client | nodes=%s collection=%s "
                "timeout=%ss retries=%s",
                [f"{n['protocol']}://{n['host']}:{n['port']}" for n in nodes],
                settings.TYPESENSE_COLLECTION,
                settings.TYPESENSE_TIMEOUT_SECONDS,
                settings.TYPESENSE_NUM_RETRIES,
            )
            _client = typesense.Client(
                {
                    # Multiple nodes give the client automatic failover: if one
                    # node is restarting, the request is retried against the
                    # next rather than surfacing as a search outage.
                    "nodes": nodes,
                    "api_key": _active_api_key(),
                    "connection_timeout_seconds": settings.TYPESENSE_TIMEOUT_SECONDS,
                    "num_retries": settings.TYPESENSE_NUM_RETRIES,
                    "retry_interval_seconds": settings.TYPESENSE_RETRY_INTERVAL_SECONDS,
                }
            )

    return _client


def reset_typesense_client() -> None:
    """
    Drop the cached client so the next call rebuilds it.

    Used by tests (to swap in a fake) and by the shutdown hook. Not needed in
    normal request handling.
    """
    global _client
    with _client_lock:
        _client = None


async def run_typesense(operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Execute a blocking TypeSense call off the event loop and normalize errors.

    Usage:
        client = get_typesense_client()
        result = await run_typesense(
            client.collections[name].documents.search, search_params
        )

    Raises:
        TypesenseUnavailable: connection, auth, timeout or 5xx failures — the
            caller should fall back to Postgres.
        ObjectNotFound: passed through untouched. "Collection does not exist"
            is a distinct, actionable condition (run the bootstrap/import), not
            a transport failure, so callers get to handle it separately.
        TypesenseClientError: other 4xx (e.g. a malformed query we built). Also
            passed through — that is OUR bug and should be visible in Sentry,
            not silently swallowed as a fallback.
    """
    try:
        return await run_in_threadpool(operation, *args, **kwargs)

    except ObjectNotFound:
        raise

    except (RequestUnauthorized, RequestForbidden) as exc:
        # Almost always a rotated/wrong API key. Log loudly — this will not
        # heal on its own, unlike a transient network blip.
        logger.error("TypeSense rejected our API key: %s", exc)
        raise TypesenseUnavailable("TypeSense authentication failed") from exc

    except (Timeout, ServiceUnavailable) as exc:
        logger.warning("TypeSense unavailable (timeout/503): %s", exc)
        raise TypesenseUnavailable("TypeSense is temporarily unavailable") from exc

    except ConnectionError as exc:
        # requests raises this for DNS/TCP failures before TypeSense's own
        # exception layer ever sees the response.
        logger.warning("Could not connect to TypeSense: %s", exc)
        raise TypesenseUnavailable("Could not connect to TypeSense") from exc

    except TypesenseClientError:
        # 4xx/5xx we did not classify above. Surface it — see docstring.
        logger.exception("Unexpected TypeSense client error")
        raise


async def check_typesense_connection() -> bool:
    """
    Verify we can actually USE TypeSense. Never raises.

    Deliberately does NOT use `operations.is_healthy`. That maps to TypeSense's
    `GET /health`, which is UNAUTHENTICATED — it returns true for a wrong,
    revoked or empty API key. A preflight built on it reports "reachable" and
    then the very next call fails with 401, which is worse than no check at
    all because it points the operator at the network instead of the key.

    So both strategies below make an authenticated call, and which one applies
    depends on the key in use:

    1. `collections.retrieve()` — lists collections. Requires an ADMIN key.
    2. A one-hit search on the configured collection — what a SEARCH-ONLY key
       is allowed to do, and exactly what the running app needs.

    A search-only key is the right key for the web process (if the environment
    leaks, it cannot delete the index). It gets a 401 from strategy 1, so
    falling through to strategy 2 checks the permission the app actually uses
    rather than one it never needs.
    """
    if not is_configured():
        return False

    try:
        client = get_typesense_client()
    except TypesenseUnavailable:
        return False

    try:
        await run_typesense(client.collections.retrieve)
        return True
    except TypesenseUnavailable:
        # Either an auth rejection (search-only key) or a genuinely unreachable
        # cluster. Strategy 2 tells the two apart.
        pass
    except Exception as exc:  # noqa: BLE001 — a health check must never raise
        logger.warning("TypeSense health check failed: %s", exc)
        return False

    try:
        await run_typesense(
            client.collections[settings.TYPESENSE_COLLECTION].documents.search,
            {"q": "*", "query_by": "name", "per_page": 1, "include_fields": "id"},
        )
        return True
    except ObjectNotFound:
        # Auth is fine — the collection just has not been created yet. That is
        # a valid state before the first import, and the caller (which creates
        # the collection) must not be blocked by it.
        logger.info(
            "TypeSense authenticated, but collection %r does not exist yet",
            settings.TYPESENSE_COLLECTION,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — a health check must never raise
        logger.warning("TypeSense health check failed: %s", exc)
        return False