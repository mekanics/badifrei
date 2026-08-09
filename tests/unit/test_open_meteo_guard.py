"""Regression: unit tests must never hit the live Open-Meteo free tier."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_unmocked_open_meteo_get_is_blocked():
    """Real ClientSession.get against open-meteo.com must raise in unit tests."""
    from ml.weather import FORECAST_URL, _open_meteo_get

    with pytest.raises(RuntimeError, match="Open-Meteo"):
        await _open_meteo_get(
            FORECAST_URL,
            {
                "latitude": 47.37,
                "longitude": 8.55,
                "hourly": "temperature_2m",
                "start_date": "2024-07-15",
                "end_date": "2024-07-15",
                "timezone": "UTC",
            },
        )
