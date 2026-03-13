"""Unit tests for TASK-023: weather DB persistence layer.

All DB calls are mocked — no live database required.
"""
import datetime
from unittest.mock import AsyncMock, MagicMock, patch, call

import numpy as np
import pandas as pd
import pytest

SAMPLE_DATE_A = datetime.date(2024, 6, 1)
SAMPLE_DATE_B = datetime.date(2024, 6, 2)
SAMPLE_DATE_C = datetime.date(2024, 6, 3)


def make_weather_rows(date: datetime.date, temp: float = 20.0) -> list[dict]:
    """24 rows of weather data for a given date."""
    return [
        {
            "date": date,
            "hour": h,
            "temperature_c": temp + h * 0.1,
            "precipitation_mm": 0.0,
            "weathercode": 0,
        }
        for h in range(24)
    ]


def make_weather_df(date: datetime.date, temp: float = 20.0) -> pd.DataFrame:
    """DataFrame of 24 rows for the given date."""
    return pd.DataFrame(make_weather_rows(date, temp))


def make_nan_df(date: datetime.date) -> pd.DataFrame:
    """NaN-filled fallback DataFrame (as returned on HTTP errors)."""
    return pd.DataFrame(
        {
            "date": [date] * 24,
            "hour": list(range(24)),
            "temperature_c": [np.nan] * 24,
            "precipitation_mm": [np.nan] * 24,
            "weathercode": [np.nan] * 24,
        }
    )


@pytest.fixture(autouse=True)
def clear_weather_cache():
    """Clear in-memory cache before/after each test."""
    from ml.weather import clear_cache
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# DB-fetch helpers
# ---------------------------------------------------------------------------

class TestWeatherDbLoad:
    """_load_dates_from_db returns dates that are already cached in DB."""

    async def test_load_dates_from_db_returns_present_dates(self):
        """Dates that exist in DB should come back from _load_dates_from_db."""
        from ml.weather import _load_dates_from_db

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"date": SAMPLE_DATE_A, "hour": h,
             "temperature_c": 20.0 + h * 0.1,
             "precipitation_mm": 0.0,
             "weathercode": 0}
            for h in range(24)
        ])

        result = await _load_dates_from_db(mock_conn, [SAMPLE_DATE_A, SAMPLE_DATE_B], city="zurich")

        assert SAMPLE_DATE_A in result
        assert SAMPLE_DATE_B not in result  # not in DB

    async def test_load_dates_from_db_empty_list(self):
        """Calling with no dates returns empty dict without hitting DB."""
        from ml.weather import _load_dates_from_db

        mock_conn = AsyncMock()
        result = await _load_dates_from_db(mock_conn, [], city="zurich")
        assert result == {}
        mock_conn.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# DB-write helpers
# ---------------------------------------------------------------------------

class TestWeatherDbPersist:
    """_persist_to_db writes rows and skips NaN-only frames."""

    async def test_persist_writes_valid_rows(self):
        """Non-NaN rows are written via executemany."""
        from ml.weather import _persist_to_db

        mock_conn = AsyncMock()
        df = make_weather_df(SAMPLE_DATE_A)
        await _persist_to_db(mock_conn, df, city="zurich")
        mock_conn.executemany.assert_called_once()

    async def test_persist_skips_nan_rows(self):
        """Frames where ALL weather columns are NaN must NOT be written."""
        from ml.weather import _persist_to_db

        mock_conn = AsyncMock()
        df = make_nan_df(SAMPLE_DATE_A)
        await _persist_to_db(mock_conn, df, city="zurich")
        # executemany should NOT be called for NaN-only data
        mock_conn.executemany.assert_not_called()

    async def test_persist_filters_individual_nan_rows(self):
        """Mixed DataFrame: only non-NaN rows are written."""
        from ml.weather import _persist_to_db

        mock_conn = AsyncMock()
        df = make_weather_df(SAMPLE_DATE_A)
        # Corrupt a few rows
        df.loc[0:3, "temperature_c"] = np.nan
        df.loc[0:3, "precipitation_mm"] = np.nan
        df.loc[0:3, "weathercode"] = np.nan

        await _persist_to_db(mock_conn, df, city="zurich")

        # Should still be called (some valid rows remain)
        mock_conn.executemany.assert_called_once()
        args = mock_conn.executemany.call_args
        # The records passed should have fewer than 24 rows (NaN rows filtered out)
        rows = args[0][1]
        assert len(rows) == 20  # 24 - 4 NaN rows

    async def test_persist_uses_on_conflict_do_nothing(self):
        """SQL statement must contain ON CONFLICT DO NOTHING."""
        from ml.weather import _persist_to_db

        mock_conn = AsyncMock()
        df = make_weather_df(SAMPLE_DATE_A)
        await _persist_to_db(mock_conn, df, city="zurich")

        sql = mock_conn.executemany.call_args[0][0]
        assert "ON CONFLICT" in sql.upper()
        assert "DO NOTHING" in sql.upper()


# ---------------------------------------------------------------------------
# fetch_weather_batch — DB integration
# ---------------------------------------------------------------------------

class TestFetchWeatherBatchDb:
    """fetch_weather_batch checks DB first, fetches missing, persists new rows."""

    async def test_fetch_weather_batch_writes_to_db(self):
        """On empty DB, fetched rows are persisted."""
        from ml.weather import fetch_weather_batch

        weather_df = make_weather_df(SAMPLE_DATE_A)
        # Ensure 'date' column present (batch adds it)
        weather_df["date"] = SAMPLE_DATE_A

        mock_conn = AsyncMock()
        # DB has no cached dates
        mock_conn.fetch = AsyncMock(return_value=[])

        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)), \
             patch("ml.weather.fetch_weather", AsyncMock(return_value=make_weather_df(SAMPLE_DATE_A))):
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        # executemany should have been called (writing new rows)
        mock_conn.executemany.assert_called()
        assert len(result) == 24

    async def test_fetch_weather_batch_reads_from_db(self):
        """Date already in DB: returned from DB, zero HTTP requests."""
        from ml.weather import fetch_weather_batch

        db_records = [
            {"date": SAMPLE_DATE_A, "hour": h,
             "temperature_c": 20.0, "precipitation_mm": 0.0, "weathercode": 0}
            for h in range(24)
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=db_records)

        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)), \
             patch("ml.weather.fetch_weather") as mock_http:
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        mock_http.assert_not_called()
        assert len(result) == 24

    async def test_fetch_weather_batch_partial_cache_hit(self):
        """DB has A and C; only B is fetched from Open-Meteo."""
        from ml.weather import fetch_weather_batch

        db_records = (
            [{"date": SAMPLE_DATE_A, "hour": h,
              "temperature_c": 20.0, "precipitation_mm": 0.0, "weathercode": 0}
             for h in range(24)]
            + [{"date": SAMPLE_DATE_C, "hour": h,
                "temperature_c": 22.0, "precipitation_mm": 0.0, "weathercode": 0}
               for h in range(24)]
        )

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=db_records)

        async def fake_fetch_weather(d):
            return make_weather_df(d)

        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)), \
             patch("ml.weather.fetch_weather", side_effect=fake_fetch_weather) as mock_http:
            result = await fetch_weather_batch([SAMPLE_DATE_A, SAMPLE_DATE_B, SAMPLE_DATE_C])

        # Only date B should trigger an HTTP call
        assert mock_http.call_count == 1
        assert mock_http.call_args[0][0] == SAMPLE_DATE_B

        # All 3 dates present in result
        assert len(result) == 72  # 3 × 24 rows

    async def test_nan_rows_not_persisted(self):
        """HTTP 500 for a date → NaN fallback returned but NOT written to DB."""
        from ml.weather import fetch_weather_batch
        import numpy as np

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # DB empty

        async def fake_fetch_weather_nan(d):
            df = make_weather_df(d)
            df["temperature_c"] = np.nan
            df["precipitation_mm"] = np.nan
            df["weathercode"] = np.nan
            return df

        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)), \
             patch("ml.weather.fetch_weather", side_effect=fake_fetch_weather_nan):
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        # NaN rows returned to caller
        assert len(result) == 24
        assert result["temperature_c"].isna().all()

        # But NOT written to DB
        mock_conn.executemany.assert_not_called()

    async def test_in_memory_cache_bypasses_db(self):
        """Dates already in _cache skip both DB and HTTP."""
        from ml.weather import fetch_weather_batch, _cache

        # Seed in-memory cache directly (key is now (city, date))
        _cache[("zurich", SAMPLE_DATE_A)] = make_weather_df(SAMPLE_DATE_A)

        mock_conn = AsyncMock()

        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)), \
             patch("ml.weather.fetch_weather") as mock_http:
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        mock_conn.fetch.assert_not_called()
        mock_http.assert_not_called()
        assert len(result) == 24


# ---------------------------------------------------------------------------
# clear_cache — extended to support DB truncation
# ---------------------------------------------------------------------------

class TestClearCache:
    def test_clear_cache_clears_in_memory(self):
        """clear_cache() always wipes the in-memory dict."""
        from ml.weather import clear_cache, _cache

        _cache[SAMPLE_DATE_A] = make_weather_df(SAMPLE_DATE_A)
        clear_cache()
        assert len(_cache) == 0

    async def test_clear_cache_db_truncates_when_flag_set(self, monkeypatch):
        """With WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR=true, clear_cache_db() truncates table."""
        monkeypatch.setenv("WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR", "true")

        from ml.weather import clear_cache_db

        mock_conn = AsyncMock()
        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)):
            await clear_cache_db()

        mock_conn.execute.assert_called()
        sql = mock_conn.execute.call_args[0][0]
        assert "TRUNCATE" in sql.upper() and "hourly_weather" in sql.lower()

    async def test_clear_cache_db_skips_truncate_without_flag(self, monkeypatch):
        """Without the env flag, clear_cache_db() only clears in-memory cache."""
        monkeypatch.delenv("WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR", raising=False)

        from ml.weather import clear_cache_db, _cache
        _cache[SAMPLE_DATE_A] = make_weather_df(SAMPLE_DATE_A)

        mock_conn = AsyncMock()
        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)):
            await clear_cache_db()

        assert len(_cache) == 0  # in-memory cleared
        mock_conn.execute.assert_not_called()  # DB NOT truncated
