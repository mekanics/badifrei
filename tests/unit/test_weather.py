"""Unit tests for ml/weather.py — all HTTP is mocked."""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest


SAMPLE_DATE = datetime.date(2024, 7, 15)  # historical date (before today - 5d)
FUTURE_DATE = datetime.date(2099, 1, 1)   # definitely future

SAMPLE_RESPONSE = {
    "hourly": {
        "time": [f"2024-07-15T{h:02d}:00" for h in range(24)],
        "temperature_2m": [20.0 + h * 0.5 for h in range(24)],
        "precipitation": [0.0] * 20 + [1.5, 2.0, 0.5, 0.0],
        "weathercode": [0] * 20 + [61, 63, 51, 0],
    }
}


def make_mock_session(status: int = 200, json_data: dict | None = None):
    """Create a mock aiohttp ClientSession context manager."""
    if json_data is None:
        json_data = SAMPLE_RESPONSE

    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_get = MagicMock(return_value=mock_resp)

    mock_session = AsyncMock()
    mock_session.get = mock_get
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    return mock_session


@pytest.fixture(autouse=True)
def clear_weather_cache():
    """Clear the weather cache before each test."""
    from ml.weather import clear_cache
    clear_cache()
    yield
    clear_cache()


class TestFetchWeatherColumns:
    async def test_returns_correct_columns(self):
        from ml.weather import fetch_weather
        with patch("aiohttp.ClientSession", return_value=make_mock_session()):
            df = await fetch_weather(SAMPLE_DATE)
        assert set(df.columns) == {"hour", "temperature_c", "precipitation_mm", "weathercode"}

    async def test_returns_24_rows(self):
        from ml.weather import fetch_weather
        with patch("aiohttp.ClientSession", return_value=make_mock_session()):
            df = await fetch_weather(SAMPLE_DATE)
        assert len(df) == 24

    async def test_hour_range_is_0_to_23(self):
        from ml.weather import fetch_weather
        with patch("aiohttp.ClientSession", return_value=make_mock_session()):
            df = await fetch_weather(SAMPLE_DATE)
        assert df["hour"].min() == 0
        assert df["hour"].max() == 23

    async def test_temperature_values_parsed(self):
        from ml.weather import fetch_weather
        with patch("aiohttp.ClientSession", return_value=make_mock_session()):
            df = await fetch_weather(SAMPLE_DATE)
        assert df["temperature_c"].iloc[0] == pytest.approx(20.0)

    async def test_precipitation_values_parsed(self):
        from ml.weather import fetch_weather
        with patch("aiohttp.ClientSession", return_value=make_mock_session()):
            df = await fetch_weather(SAMPLE_DATE)
        # Hours 20-22 should have non-zero precipitation
        assert df.loc[df["hour"] == 20, "precipitation_mm"].values[0] == pytest.approx(1.5)


class TestFetchWeatherErrorHandling:
    async def test_http_error_returns_nan_df(self):
        from ml.weather import fetch_weather
        with patch("aiohttp.ClientSession", return_value=make_mock_session(status=500)):
            df = await fetch_weather(SAMPLE_DATE)
        assert len(df) == 24
        assert df["temperature_c"].isna().all()
        assert df["precipitation_mm"].isna().all()

    async def test_network_exception_returns_nan_df(self):
        from ml.weather import fetch_weather
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))
        mock_session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            df = await fetch_weather(SAMPLE_DATE)
        assert len(df) == 24
        assert df["temperature_c"].isna().all()

    async def test_nan_df_has_correct_columns(self):
        from ml.weather import fetch_weather
        with patch("aiohttp.ClientSession", return_value=make_mock_session(status=404)):
            df = await fetch_weather(SAMPLE_DATE)
        assert set(df.columns) == {"hour", "temperature_c", "precipitation_mm", "weathercode"}


class TestFetchWeatherCaching:
    async def test_second_call_uses_cache(self):
        from ml.weather import fetch_weather
        mock_session = make_mock_session()
        with patch("aiohttp.ClientSession", return_value=mock_session) as mock_cls:
            await fetch_weather(SAMPLE_DATE)
            await fetch_weather(SAMPLE_DATE)
        # ClientSession should only have been constructed once (or get called once)
        assert mock_cls.call_count == 1

    async def test_different_dates_not_cached_together(self):
        from ml.weather import fetch_weather
        date_a = datetime.date(2024, 7, 15)
        date_b = datetime.date(2024, 7, 16)

        response_b = {
            "hourly": {
                "time": [f"2024-07-16T{h:02d}:00" for h in range(24)],
                "temperature_2m": [25.0] * 24,
                "precipitation": [0.0] * 24,
                "weathercode": [0] * 24,
            }
        }

        with patch("aiohttp.ClientSession", return_value=make_mock_session(json_data=SAMPLE_RESPONSE)):
            df_a = await fetch_weather(date_a)
        with patch("aiohttp.ClientSession", return_value=make_mock_session(json_data=response_b)):
            df_b = await fetch_weather(date_b)

        # Temperatures differ — distinct DataFrames were fetched
        assert df_a["temperature_c"].iloc[0] != df_b["temperature_c"].iloc[0]


class TestUrlSelection:
    def test_future_date_uses_forecast_url(self):
        from ml.weather import _select_url, FORECAST_URL
        assert _select_url(FUTURE_DATE) == FORECAST_URL

    def test_old_historical_date_uses_archive_url(self):
        from ml.weather import _select_url, ARCHIVE_URL
        old_date = datetime.date(2023, 1, 1)
        assert _select_url(old_date) == ARCHIVE_URL

    def test_today_uses_forecast_url(self):
        from ml.weather import _select_url, FORECAST_URL
        assert _select_url(datetime.date.today()) == FORECAST_URL
