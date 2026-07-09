"""Health/readiness endpoint tests -- these must never fail in production,
since load balancers depend on them to route traffic correctly."""
import pytest


@pytest.mark.asyncio
async def test_liveness(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness(client):
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["cache"] is True
