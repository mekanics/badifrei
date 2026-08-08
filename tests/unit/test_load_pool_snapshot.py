"""load_pool_snapshot must query a single pool, not the full site snapshot."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from api.main import get_pools, load_pool_snapshot

Z = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 8, 8, 14, 0, tzinfo=Z)


@pytest.mark.asyncio
async def test_load_pool_snapshot_uses_single_uid_query(monkeypatch):
    pool = next(p for p in get_pools() if p["uid"] == "fb006")
    row = {
        "pool_uid": "fb006",
        "current_fill": 100,
        "max_space": 200,
        "free_space": 100,
        "occupancy_pct": 50,
        "time": NOW,
    }
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=row)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=db)))

    monkeypatch.setattr(
        "api.main._fetch_latest_observation",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.main._fetch_city_weather_hints",
        AsyncMock(return_value={}),
    )

    item = await load_pool_snapshot(request, pool)
    assert item is not None
    assert item["pool_uid"] == "fb006"
    assert item["occupancy_pct"] == 50
    assert "is_open" in item

    db.fetchrow.assert_awaited_once()
    sql = db.fetchrow.await_args.args[0]
    assert "WHERE pool_uid = $1" in sql
    assert db.fetchrow.await_args.args[1] == "fb006"
    assert "DISTINCT ON" not in sql


@pytest.mark.asyncio
async def test_load_pool_snapshot_none_without_db():
    pool = {"uid": "fb006", "name": "X", "city": "zurich"}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=None)))
    assert await load_pool_snapshot(request, pool) is None
