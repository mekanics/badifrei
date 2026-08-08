"""Detail page renders weather_temps in the header."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from ml.opening_hours import WeatherHint

from api.snapshots import LatestStatus

Z = ZoneInfo("Europe/Zurich")


@pytest.fixture
async def client():
    from api.main import app

    app.state.db_pool = MagicMock(name="db_pool")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.state.db_pool = None


def _patch_temps(monkeypatch, *, status, weather_by_city):
    monkeypatch.setattr(
        "api.routers.pages._fetch_latest_status",
        AsyncMock(return_value=status),
    )
    monkeypatch.setattr(
        "api.routers.pages._fetch_city_weather_hints",
        AsyncMock(return_value=weather_by_city),
    )
    monkeypatch.setattr(
        "api.routers.pages._latest_max_space",
        AsyncMock(return_value=500),
    )


def _fresh_status(*, water_temp_c: float | None) -> LatestStatus:
    """Timestamps relative to real now so freshness gates pass in the handler."""
    now = datetime.now(tz=Z)
    return LatestStatus(
        observation=None,
        water_temp_c=water_temp_c,
        observed_at=now - timedelta(minutes=6),
        source_modified_at=now - timedelta(hours=9),
    )


@pytest.mark.asyncio
async def test_detail_page_renders_water_and_air(client, monkeypatch):
    _patch_temps(
        monkeypatch,
        status=_fresh_status(water_temp_c=26.0),
        weather_by_city={
            "zurich": WeatherHint(
                temperature_c=21.5, precipitation_mm=0.0, weathercode=0
            )
        },
    )
    resp = await client.get("/bad/fb006")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="detail-weather-temps"' in html
    assert "26°" in html
    assert "Wasser" in html
    assert "22°" in html  # 21.5 rounds to 22
    assert "Luft" in html
    assert 'aria-hidden="true"' in html
    assert "visually-hidden" in html


@pytest.mark.asyncio
async def test_detail_page_air_only_for_hallenbad(client, monkeypatch):
    _patch_temps(
        monkeypatch,
        status=_fresh_status(water_temp_c=None),
        weather_by_city={
            "zurich": WeatherHint(
                temperature_c=18.0, precipitation_mm=0.0, weathercode=3
            )
        },
    )
    resp = await client.get("/bad/SSD-2")
    assert resp.status_code == 200
    html = resp.text
    match = re.search(
        r'<span class="detail-weather-temps"[^>]*>(.*?)</span>\s*'
        r'<span class="detail-live-count"',
        html,
        re.DOTALL,
    )
    assert match is not None
    block = match.group(1)
    assert "Wasser" not in block
    assert "Luft" in block
    assert "18°" in block


@pytest.mark.asyncio
async def test_detail_page_keeps_hidden_temps_slot_when_no_data(client, monkeypatch):
    """Element must exist (hidden) so live refresh can populate later."""
    _patch_temps(
        monkeypatch,
        status=None,
        weather_by_city={},
    )
    resp = await client.get("/bad/fb006")
    assert resp.status_code == 200
    assert 'id="detail-weather-temps"' in resp.text
    assert re.search(
        r'id="detail-weather-temps"[^>]*\bhidden\b',
        resp.text,
    )


@pytest.mark.asyncio
async def test_detail_page_renders_when_weather_lookup_raises(client, monkeypatch):
    monkeypatch.setattr(
        "api.routers.pages._fetch_city_weather_hints",
        AsyncMock(side_effect=RuntimeError("weather down")),
    )
    monkeypatch.setattr(
        "api.routers.pages._fetch_latest_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.routers.pages._latest_max_space",
        AsyncMock(return_value=None),
    )
    resp = await client.get("/bad/fb006")
    assert resp.status_code == 200
    assert "Freibad Allenmoos" in resp.text
