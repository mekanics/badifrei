"""_fetch_latest_status + load_current_snapshot water-temp freshness."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from api.snapshots import (
    _fetch_latest_observation,
    _fetch_latest_status,
    load_current_snapshot,
)

Z = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 8, 8, 14, 0, tzinfo=Z)


def _open_status(_pool, _now, **_kwargs):
    return {
        "is_open": True,
        "next_open": None,
        "opens_seasonal": None,
        "state": "open_guaranteed",
        "reason": None,
        "confidence": "official_structured",
    }


@pytest.mark.asyncio
async def test_fetch_latest_status_single_query_includes_water_temp():
    db = AsyncMock()
    db.fetchrow = AsyncMock(
        return_value={
            "status_text": "offen",
            "water_temp_c": 26.0,
            "source_modified_at": NOW,
            "observed_at": NOW,
        }
    )
    status = await _fetch_latest_status(db, "fb006")
    assert status is not None
    assert status.water_temp_c == 26.0
    assert status.observed_at == NOW
    assert status.source_modified_at == NOW
    assert status.observation is not None
    assert status.observation.is_open is True

    db.fetchrow.assert_awaited_once()
    sql = db.fetchrow.await_args.args[0]
    assert "water_temp_c" in sql
    assert "WHERE pool_uid = $1" in sql
    assert db.fetchrow.await_args.args[1] == "fb006"


@pytest.mark.asyncio
async def test_fetch_latest_status_none_when_no_row():
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=None)
    assert await _fetch_latest_status(db, "fb006") is None


@pytest.mark.asyncio
async def test_fetch_latest_observation_is_thin_wrapper():
    db = AsyncMock()
    db.fetchrow = AsyncMock(
        return_value={
            "status_text": "geschlossen",
            "water_temp_c": 24.0,
            "source_modified_at": NOW,
            "observed_at": NOW,
        }
    )
    obs = await _fetch_latest_observation(db, "fb006")
    assert obs is not None
    assert obs.is_open is False
    db.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_current_snapshot_gates_stale_water_temp(monkeypatch):
    """Stale observed_at must not appear on /api/current water_temp_c."""
    now = datetime.now(tz=Z)
    fresh_obs = now - timedelta(minutes=6)
    stale_obs = now - timedelta(minutes=90)
    source = now - timedelta(hours=9)

    status_rows = [
        {
            "pool_uid": "fb006",
            "status_text": "offen",
            "water_temp_c": 26.0,
            "observed_at": fresh_obs,
            "source_modified_at": source,
        },
        {
            "pool_uid": "SSD-2",
            "status_text": "offen",
            "water_temp_c": 24.0,
            "observed_at": stale_obs,
            "source_modified_at": source,
        },
    ]

    async def _fetch(sql, *_args):
        if "FROM pool_occupancy" in sql:
            return []
        if "FROM pool_status" in sql:
            return status_rows
        return []

    db = AsyncMock()
    db.fetch = AsyncMock(side_effect=_fetch)

    monkeypatch.setattr(
        "api.snapshots.get_pools",
        lambda: [
            {"uid": "fb006", "name": "Freibad Allenmoos", "city": "zurich"},
            {"uid": "SSD-2", "name": "Hallenbad Blaesi", "city": "zurich"},
        ],
    )
    monkeypatch.setattr(
        "api.snapshots._fetch_city_weather_hints",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr("api.snapshots._compute_pool_is_open", _open_status)

    items = await load_current_snapshot(db)
    by_uid = {i["pool_uid"]: i for i in items}
    assert by_uid["fb006"]["water_temp_c"] == 26.0
    assert by_uid["SSD-2"]["water_temp_c"] is None
