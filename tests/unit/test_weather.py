"""Unit tests for ml/weather.py — all HTTP is mocked."""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest


SAMPLE_DATE = datetime.date(2024, 7, 15)  # historical date (before today - 5d)
FUTURE_DATE = datetime.date(2099, 1, 1)  # definitely future

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
def clear_weather_cache(monkeypatch):
    """Clear the weather cache before each test; disable HTTP pacing in unit tests."""
    import ml.weather as weather
    from ml.weather import clear_cache

    monkeypatch.setattr(weather, "OPEN_METEO_MIN_INTERVAL_S", 0.0)
    clear_cache()
    yield
    clear_cache()


class TestFetchWeatherColumns:
    async def test_returns_correct_columns(self):
        from ml.weather import fetch_weather

        with patch("aiohttp.ClientSession", return_value=make_mock_session()):
            df = await fetch_weather(SAMPLE_DATE)
        assert set(df.columns) == {
            "hour",
            "temperature_c",
            "precipitation_mm",
            "weathercode",
        }

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
        assert df.loc[df["hour"] == 20, "precipitation_mm"].values[0] == pytest.approx(
            1.5
        )


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
        assert set(df.columns) == {
            "hour",
            "temperature_c",
            "precipitation_mm",
            "weathercode",
        }


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

        with patch(
            "aiohttp.ClientSession",
            return_value=make_mock_session(json_data=SAMPLE_RESPONSE),
        ):
            df_a = await fetch_weather(date_a)
        with patch(
            "aiohttp.ClientSession",
            return_value=make_mock_session(json_data=response_b),
        ):
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
        from ml.weather import FORECAST_URL, _select_url, _utc_today

        assert _select_url(_utc_today()) == FORECAST_URL


class TestArchiveEligible:
    def test_boundary_at_lag_days(self):
        from ml.weather import ARCHIVE_LAG_DAYS, is_archive_eligible

        today = datetime.date(2026, 8, 8)
        assert (
            is_archive_eligible(
                today - datetime.timedelta(days=ARCHIVE_LAG_DAYS + 1), today=today
            )
            is True
        )
        assert (
            is_archive_eligible(
                today - datetime.timedelta(days=ARCHIVE_LAG_DAYS), today=today
            )
            is False
        )

    def test_default_today_uses_utc_today_helper(self):
        """Eligibility must use UTC calendar date (hourly_weather is UTC-keyed)."""
        from ml.weather import is_archive_eligible

        with patch("ml.weather._utc_today", return_value=datetime.date(2026, 8, 8)):
            # lag=5 → boundary date 2026-08-03 is NOT eligible; 2026-08-02 is
            assert is_archive_eligible(datetime.date(2026, 8, 3)) is False
            assert is_archive_eligible(datetime.date(2026, 8, 2)) is True

    def test_utc_today_matches_utc_clock(self):
        from ml.weather import _utc_today

        assert _utc_today() == datetime.datetime.now(datetime.timezone.utc).date()

    def test_select_url_matches_eligibility(self):
        from ml.weather import (
            ARCHIVE_URL,
            FORECAST_URL,
            _select_url,
            _utc_today,
            is_archive_eligible,
        )

        today = _utc_today()
        old = today - datetime.timedelta(days=10)
        recent = today - datetime.timedelta(days=2)
        assert is_archive_eligible(old) is True
        assert _select_url(old) == ARCHIVE_URL
        assert is_archive_eligible(recent) is False
        assert _select_url(recent) == FORECAST_URL


class TestForecastDayFresh:
    def test_null_fetched_at_is_stale(self):
        from ml.weather import _is_forecast_day_fresh

        now = datetime.datetime(2026, 8, 8, 12, 0, tzinfo=datetime.timezone.utc)
        df = pd.DataFrame(
            {
                "hour": list(range(24)),
                "source": ["forecast"] * 24,
                "fetched_at": [None] * 24,
            }
        )
        assert _is_forecast_day_fresh(df, now=now) is False

    def test_within_ttl_is_fresh(self):
        from ml.weather import FORECAST_WEATHER_MAX_AGE, _is_forecast_day_fresh

        now = datetime.datetime(2026, 8, 8, 12, 0, tzinfo=datetime.timezone.utc)
        fetched = now - FORECAST_WEATHER_MAX_AGE / 2
        df = pd.DataFrame(
            {
                "hour": list(range(24)),
                "source": ["forecast"] * 24,
                "fetched_at": [fetched] * 24,
            }
        )
        assert _is_forecast_day_fresh(df, now=now) is True

    def test_older_than_ttl_is_stale(self):
        from ml.weather import FORECAST_WEATHER_MAX_AGE, _is_forecast_day_fresh

        now = datetime.datetime(2026, 8, 8, 12, 0, tzinfo=datetime.timezone.utc)
        fetched = now - FORECAST_WEATHER_MAX_AGE - datetime.timedelta(seconds=1)
        df = pd.DataFrame(
            {
                "hour": list(range(24)),
                "source": ["forecast"] * 24,
                "fetched_at": [fetched] * 24,
            }
        )
        assert _is_forecast_day_fresh(df, now=now) is False


class TestLiveWeatherStale:
    def test_null_fetched_at_is_stale(self):
        from ml.weather import is_live_weather_stale

        now = datetime.datetime(2026, 8, 8, 19, 0, tzinfo=datetime.timezone.utc)
        assert is_live_weather_stale(None, now=now) is True

    def test_fresh_within_ttl(self):
        from ml.weather import LIVE_WEATHER_MAX_AGE, is_live_weather_stale

        now = datetime.datetime(2026, 8, 8, 19, 0, tzinfo=datetime.timezone.utc)
        fetched = now - LIVE_WEATHER_MAX_AGE + datetime.timedelta(seconds=1)
        assert is_live_weather_stale(fetched, now=now) is False

    def test_exactly_at_ttl_is_stale(self):
        from ml.weather import LIVE_WEATHER_MAX_AGE, is_live_weather_stale

        now = datetime.datetime(2026, 8, 8, 19, 0, tzinfo=datetime.timezone.utc)
        fetched = now - LIVE_WEATHER_MAX_AGE
        assert is_live_weather_stale(fetched, now=now) is True


class TestRefreshLiveHour:
    async def test_clears_in_memory_cache_for_city_date(self):
        """Stale day frames must not short-circuit the live HTTP refresh."""
        from ml.weather import _cache, refresh_live_hour

        when = datetime.datetime(
            2026, 8, 8, 21, 15, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
        )
        lookup_date = datetime.date(2026, 8, 8)  # 19:00 UTC
        stale_df = pd.DataFrame(
            {
                "hour": list(range(24)),
                "temperature_c": [21.5] * 24,
                "precipitation_mm": [0.0] * 24,
                "weathercode": [1] * 24,
            }
        )
        _cache[("zurich", lookup_date)] = stale_df

        fresh_df = pd.DataFrame(
            {
                "hour": list(range(24)),
                "temperature_c": [29.6] * 24,
                "precipitation_mm": [0.0] * 24,
                "weathercode": [0] * 24,
            }
        )
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)  # no fresh row under lock

        with (
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                return_value=fresh_df,
            ) as mock_fetch,
            patch(
                "ml.weather._get_db_conn",
                new_callable=AsyncMock,
                return_value=mock_conn,
            ),
        ):
            result = await refresh_live_hour("zurich", when)

        mock_fetch.assert_awaited_once_with(lookup_date, city="zurich")
        assert result is not None
        assert result["temperature_c"] == pytest.approx(29.6)
        assert result["weathercode"] == 0
        execute_sqls = [c[0][0].upper() for c in mock_conn.execute.await_args_list]
        assert any("PG_ADVISORY_LOCK" in s for s in execute_sqls)
        assert any("PG_ADVISORY_UNLOCK" in s for s in execute_sqls)
        assert any("DO UPDATE" in s for s in execute_sqls)
        # Stale day frame must not remain after refresh.
        assert ("zurich", lookup_date) not in _cache

    async def test_skips_http_when_row_fresh_under_advisory_lock(self):
        """Second worker / waiter: lock + fresh fetched_at → no Open-Meteo call."""
        from ml.weather import refresh_live_hour

        when = datetime.datetime(2026, 8, 8, 19, 15, tzinfo=datetime.timezone.utc)
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "temperature_c": 29.0,
                "precipitation_mm": 0.0,
                "weathercode": 0,
                "fetched_at": when - datetime.timedelta(minutes=1),
            }
        )

        with (
            patch("ml.weather.fetch_weather", new_callable=AsyncMock) as mock_fetch,
            patch(
                "ml.weather._get_db_conn",
                new_callable=AsyncMock,
                return_value=mock_conn,
            ),
        ):
            result = await refresh_live_hour("zurich", when)

        mock_fetch.assert_not_called()
        assert result["temperature_c"] == pytest.approx(29.0)
        assert result["weathercode"] == 0
        # Still lock/unlock, but no upsert
        execute_sqls = [c[0][0].upper() for c in mock_conn.execute.await_args_list]
        assert any("PG_ADVISORY_LOCK" in s for s in execute_sqls)
        assert any("PG_ADVISORY_UNLOCK" in s for s in execute_sqls)
        assert not any("DO UPDATE" in s for s in execute_sqls)

    async def test_unknown_city_returns_none(self):
        from ml.weather import refresh_live_hour

        when = datetime.datetime(2026, 8, 8, 19, 0, tzinfo=datetime.timezone.utc)
        assert await refresh_live_hour("not-a-city", when) is None


class TestOpenMeteoPacing:
    async def test_serializes_concurrent_http(self, monkeypatch):
        """Free tier: at most one in-flight Open-Meteo GET per process."""
        import asyncio

        import ml.weather as weather
        from ml.weather import fetch_weather

        monkeypatch.setattr(weather, "OPEN_METEO_MIN_INTERVAL_S", 0.0)
        weather._cache.clear()
        weather._last_http_mono = 0.0

        in_flight = 0
        max_in_flight = 0

        class SlowResp:
            status = 200

            async def __aenter__(self):
                nonlocal in_flight, max_in_flight
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                await asyncio.sleep(0.05)
                return self

            async def __aexit__(self, *_args):
                nonlocal in_flight
                in_flight -= 1
                return False

            async def json(self):
                return SAMPLE_RESPONSE

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=lambda *_a, **_k: SlowResp())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        d1 = datetime.date(2024, 7, 15)
        d2 = datetime.date(2024, 7, 16)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            await asyncio.gather(fetch_weather(d1), fetch_weather(d2))

        assert max_in_flight == 1

    async def test_enforces_min_interval_between_starts(self, monkeypatch):
        import time

        import ml.weather as weather
        from ml.weather import fetch_weather

        monkeypatch.setattr(weather, "OPEN_METEO_MIN_INTERVAL_S", 0.08)
        weather._cache.clear()
        weather._last_http_mono = 0.0

        starts: list[float] = []

        class TimedResp:
            status = 200

            async def __aenter__(self):
                starts.append(time.monotonic())
                return self

            async def __aexit__(self, *_args):
                return False

            async def json(self):
                return SAMPLE_RESPONSE

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=lambda *_a, **_k: TimedResp())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        d1 = datetime.date(2024, 7, 15)
        d2 = datetime.date(2024, 7, 16)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            await fetch_weather(d1)
            await fetch_weather(d2)

        assert len(starts) == 2
        assert starts[1] - starts[0] >= 0.07
