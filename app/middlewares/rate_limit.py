"""
Rate limiting via slowapi, backed by Valkey (so limits are shared across
ALL app instances -- an in-memory limiter would let a client bypass the
limit just by hitting a different instance behind the load balancer).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.VALKEY_URL,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
