"""DbPool Depends reads app.state.db_pool (ADR-002)."""

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_db_pool_depends_passes_app_state_pool():
    from api.main import app

    sentinel = MagicMock(name="db_pool")
    app.state.db_pool = sentinel

    captured = {}

    async def _fake_snapshot(db_pool):
        captured["db_pool"] = db_pool
        return []

    from api.routers import json_api

    original = json_api.load_current_snapshot
    json_api.load_current_snapshot = _fake_snapshot
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/current")
        assert resp.status_code == 200
        assert captured["db_pool"] is sentinel
    finally:
        json_api.load_current_snapshot = original
        app.state.db_pool = None


@pytest.mark.asyncio
async def test_db_pool_depends_none_when_unset():
    from api.main import app

    app.state.db_pool = None
    captured = {}

    async def _fake_snapshot(db_pool):
        captured["db_pool"] = db_pool
        return []

    from api.routers import json_api

    original = json_api.load_current_snapshot
    json_api.load_current_snapshot = _fake_snapshot
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/current")
        assert resp.status_code == 200
        assert captured["db_pool"] is None
    finally:
        json_api.load_current_snapshot = original
