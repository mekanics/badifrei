"""load_pool_snapshot must query a single pool, not the full site snapshot."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from api.catalog import get_pools
from api.snapshots import load_pool_snapshot

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

    monkeypatch.setattr(
        "api.snapshots._fetch_latest_observation",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.snapshots._fetch_city_weather_hints",
        AsyncMock(return_value={}),
    )

    item = await load_pool_snapshot(db, pool)
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
    assert await load_pool_snapshot(None, pool) is None
