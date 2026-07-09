"""
Test fixtures.

We simulate Supabase-issued JWTs by signing our own tokens with the same
SUPABASE_JWT_SECRET the app is configured with (see .env) -- this lets us
test the full auth -> profile -> onboarding -> facilities -> saved ->
inquiry -> assessment flow end-to-end without needing a live Supabase
project or network access.
"""
import asyncio
import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    """
    Session-scoped event loop. Required because app.core.database.engine
    and app.core.cache's Valkey client are module-level singletons created
    once and bound to whichever event loop first uses them -- pytest-
    asyncio's default per-test event loop would otherwise cause
    "attached to a different loop" errors on the second test that touches
    either of them.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def make_supabase_jwt(
    provider: str = "email",
    email: str = "test@example.com",
    full_name: str | None = "Test User",
    avatar_url: str | None = None,
    user_id: str | None = None,
) -> str:
    """Builds a JWT shaped like what Supabase Auth actually issues."""
    now = int(time.time())
    user_metadata = {}
    if provider == "google":
        user_metadata = {"full_name": full_name, "avatar_url": avatar_url or "https://example.com/g.jpg"}
    elif provider == "apple":
        user_metadata = {"name": full_name}
    else:
        user_metadata = {"full_name": full_name}

    payload = {
        "sub": user_id or str(uuid.uuid4()),
        "email": email,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
        "app_metadata": {"provider": provider, "providers": [provider]},
        "user_metadata": user_metadata,
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def auth_headers_factory():
    def _make(provider: str = "email", **kwargs):
        token = make_supabase_jwt(provider=provider, **kwargs)
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(scope="session", autouse=True)
async def flush_cache_before_tests():
    """
    Flushes Valkey before the test session runs. Without this, a stale
    cached response from a PREVIOUS run (or from a full data reimport that
    generated new facility ids) can make an endpoint return data for an id
    that no longer exists in the DB -- exactly the kind of "worked in dev,
    broke after a data refresh" bug this project's cache_get/cache_set
    degrade-gracefully design doesn't fully protect against on its own.
    Production ops note: flush the cache after any bulk facility reimport
    for the same reason.
    """
    from app.core.cache import get_cache_client

    client = get_cache_client()
    await client.flushdb()
    yield
