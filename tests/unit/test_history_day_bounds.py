"""Regression: /api/history day window must use Europe/Zurich midnights."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from api.catalog import ZURICH_TZ
from api.routers.json_api import _zurich_day_bounds

Z = ZoneInfo("Europe/Zurich")


class TestZurichDayBounds:
    def test_summer_cest_offset(self):
        start, end = _zurich_day_bounds(date(2026, 8, 9))
        assert start == datetime(2026, 8, 9, 0, 0, 0, tzinfo=ZURICH_TZ)
        assert start.utcoffset() == timedelta(hours=2)
        assert end == start + timedelta(days=1)
        assert end.utcoffset() == timedelta(hours=2)

    def test_winter_cet_offset(self):
        start, end = _zurich_day_bounds(date(2026, 1, 15))
        assert start == datetime(2026, 1, 15, 0, 0, 0, tzinfo=ZURICH_TZ)
        assert start.utcoffset() == timedelta(hours=1)
        assert end == start + timedelta(days=1)

    def test_cest_midnight_samples_inside_bounds(self):
        """Zurich 00:15 / 01:15 are still previous UTC day — must be in window."""
        start, end = _zurich_day_bounds(date(2026, 8, 9))
        sample_00 = datetime(2026, 8, 8, 22, 15, tzinfo=timezone.utc)
        sample_01 = datetime(2026, 8, 8, 23, 15, tzinfo=timezone.utc)
        sample_02 = datetime(2026, 8, 9, 0, 15, tzinfo=timezone.utc)
        assert start <= sample_00.astimezone(Z) < end
        assert start <= sample_01.astimezone(Z) < end
        assert start <= sample_02.astimezone(Z) < end
        # Equivalent: UTC instants must compare correctly against Zurich bounds
        assert start <= sample_00 < end
        assert start <= sample_01 < end
        assert start <= sample_02 < end


@pytest.mark.asyncio
async def test_history_passes_zurich_aware_bounds_to_fetch():
    from api.main import app

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    app.state.db_pool = mock_pool

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/history?pool_uid=SSD-5&date=2026-08-09")
        assert resp.status_code == 200
        mock_pool.fetch.assert_awaited_once()
        args = mock_pool.fetch.await_args.args
        # pool_uid, start, end
        assert args[1] == "SSD-5"
        start, end = args[2], args[3]
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)
        assert start.tzinfo is not None
        assert end.tzinfo is not None
        assert start == datetime(2026, 8, 9, 0, 0, 0, tzinfo=ZURICH_TZ)
        assert end == start + timedelta(days=1)
        assert start.utcoffset() == timedelta(hours=2)
    finally:
        app.state.db_pool = None


@pytest.mark.asyncio
async def test_history_unknown_pool_404():
    from api.main import app

    app.state.db_pool = AsyncMock()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/history?pool_uid=NOT-A-POOL&date=2026-08-09")
        assert resp.status_code == 404
    finally:
        app.state.db_pool = None


@pytest.mark.asyncio
async def test_history_invalid_date_422():
    from api.main import app

    app.state.db_pool = AsyncMock()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/history?pool_uid=SSD-5&date=not-a-date")
        assert resp.status_code == 422
    finally:
        app.state.db_pool = None
