# """
# Health endpoints -- used by the load balancer / orchestrator to decide
# whether an instance should receive traffic (readiness) and whether it
# should be restarted (liveness).
# """
# from fastapi import APIRouter

# from app.core.cache import check_cache_connection
# from app.core.database import check_db_connection
# from app.schemas.common import HealthResponse, ReadinessResponse

# router = APIRouter(tags=["health"])


# @router.get("/health", response_model=HealthResponse)
# async def liveness():
#     """Cheap check -- process is up and responding. No dependency checks."""
#     return HealthResponse(status="ok")


# @router.get("/health/ready", response_model=ReadinessResponse)
# async def readiness():
#     """
#     Checks actual dependencies (DB, cache). Load balancers should stop
#     routing traffic to an instance that fails this -- e.g. during a bad
#     deploy or a DB outage -- without killing the process outright.
#     """
#     db_ok = await check_db_connection()
#     cache_ok = await check_cache_connection()
#     overall = "ok" if (db_ok and cache_ok) else "degraded"
#     return ReadinessResponse(status=overall, database=db_ok, cache=cache_ok)



















































"""
Health endpoints -- used by the load balancer / orchestrator to decide
whether an instance should receive traffic (readiness) and whether it
should be restarted (liveness).
"""
from fastapi import APIRouter

from app.core.cache import check_cache_connection
from app.core.database import check_db_connection
from app.core.typesense import check_typesense_connection, is_configured
from app.schemas.common import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def liveness():
    """Cheap check -- process is up and responding. No dependency checks."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness():
    """
    Checks actual dependencies (DB, cache, search). Load balancers should stop
    routing traffic to an instance that fails this -- e.g. during a bad
    deploy or a DB outage -- without killing the process outright.

    TypeSense is reported but deliberately does NOT make the instance
    unhealthy. Search falls back to Postgres when TypeSense is down, so the
    instance can still serve every request correctly; pulling it out of the
    load balancer would turn a degraded search into a full outage. The flag
    exists so the problem is visible on a dashboard rather than discovered
    through user complaints.
    """
    db_ok = await check_db_connection()
    cache_ok = await check_cache_connection()
    search_ok = await check_typesense_connection()

    overall = "ok" if (db_ok and cache_ok) else "degraded"
    if overall == "ok" and is_configured() and not search_ok:
        overall = "search_degraded"

    return ReadinessResponse(
        status=overall,
        database=db_ok,
        cache=cache_ok,
        search=search_ok,
    )