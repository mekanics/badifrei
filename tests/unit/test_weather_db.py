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
def clear_weather_cache(monkeypatch):
    """Clear in-memory cache before/after each test; disable HTTP pacing."""
    import ml.weather as weather
    from ml.weather import clear_cache

    monkeypatch.setattr(weather, "OPEN_METEO_MIN_INTERVAL_S", 0.0)
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
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "date": SAMPLE_DATE_A,
                    "hour": h,
                    "temperature_c": 20.0 + h * 0.1,
                    "precipitation_mm": 0.0,
                    "weathercode": 0,
                }
                for h in range(24)
            ]
        )

        result = await _load_dates_from_db(
            mock_conn, [SAMPLE_DATE_A, SAMPLE_DATE_B], city="zurich"
        )

        assert SAMPLE_DATE_A in result
        assert SAMPLE_DATE_B not in result  # not in DB

    async def test_load_dates_from_db_empty_list(self):
        """Calling with no dates returns empty dict without hitting DB."""
        from ml.weather import _load_dates_from_db

        mock_conn = AsyncMock()
        result = await _load_dates_from_db(mock_conn, [], city="zurich")
        assert result == {}
        mock_conn.fetch.assert_not_called()

    async def test_load_dates_from_db_handles_asyncpg_record_iteration(self):
        """Regression: asyncpg.Record iterates values → KeyError('date') if naively listed."""
        from ml.weather import _load_dates_from_db

        class FakeRecord:
            def __init__(self, mapping):
                self._m = mapping

            def keys(self):
                return self._m.keys()

            def __getitem__(self, key):
                return self._m[key]

            def __iter__(self):
                return iter(self._m.values())

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                FakeRecord(
                    {
                        "date": SAMPLE_DATE_A,
                        "hour": h,
                        "temperature_c": 20.0,
                        "precipitation_mm": 0.0,
                        "weathercode": 0,
                        "source": "archive",
                    }
                )
                for h in range(24)
            ]
        )

        result = await _load_dates_from_db(mock_conn, [SAMPLE_DATE_A], city="zurich")
        assert SAMPLE_DATE_A in result
        assert len(result[SAMPLE_DATE_A]) == 24
        assert "date" in result[SAMPLE_DATE_A].columns


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

    async def test_persist_uses_forecast_upsert_protecting_archive_and_live(self):
        """Forecast writes upsert, but SQL must not clobber archive/live rows."""
        from ml.weather import _persist_to_db

        mock_conn = AsyncMock()
        df = make_weather_df(SAMPLE_DATE_A)
        await _persist_to_db(mock_conn, df, city="zurich")

        sql = mock_conn.executemany.call_args[0][0]
        assert "ON CONFLICT" in sql.upper()
        assert "DO UPDATE" in sql.upper()
        assert "ARCHIVE" in sql.upper()
        assert "LIVE" in sql.upper()
        records = mock_conn.executemany.call_args[0][1]
        assert records
        # city, date, hour, temp, precip, code, fetched_at, source
        assert records[0][7] == "forecast"


class TestLiveWeatherUpsertSql:
    """Live/archive upsert vs forecast refresh upsert."""

    def test_upsert_live_sql_is_do_update_with_live_source(self):
        from ml.weather import _UPSERT_LIVE_SQL

        sql = _UPSERT_LIVE_SQL.upper()
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql
        assert "FETCHED_AT" in sql
        assert "SOURCE" in sql
        assert "DO NOTHING" not in sql

    def test_upsert_archive_sql_is_do_update(self):
        from ml.weather import _UPSERT_ARCHIVE_SQL

        sql = _UPSERT_ARCHIVE_SQL.upper()
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql
        assert "SOURCE" in sql

    def test_forecast_upsert_sql_skips_archive_and_live(self):
        from ml.weather import _UPSERT_FORECAST_SQL

        sql = _UPSERT_FORECAST_SQL.upper()
        assert "DO UPDATE" in sql
        assert "ARCHIVE" in sql
        assert "LIVE" in sql
        assert "FETCHED_AT" in sql


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

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather",
                AsyncMock(return_value=make_weather_df(SAMPLE_DATE_A)),
            ),
        ):
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        # executemany should have been called (writing new rows)
        mock_conn.executemany.assert_called()
        assert len(result) == 24

    async def test_fetch_weather_batch_reads_from_db(self):
        """Archive-backed DB day: returned from DB, zero HTTP requests."""
        from ml.weather import fetch_weather_batch

        db_records = [
            {
                "date": SAMPLE_DATE_A,
                "hour": h,
                "temperature_c": 20.0,
                "precipitation_mm": 0.0,
                "weathercode": 0,
                "source": "archive",
            }
            for h in range(24)
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=db_records)

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch("ml.weather.fetch_weather") as mock_http,
        ):
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        mock_http.assert_not_called()
        assert len(result) == 24

    async def test_fetch_weather_batch_partial_cache_hit(self):
        """DB has archive-backed A and C; only missing B is fetched."""
        from ml.weather import fetch_weather_batch

        db_records = [
            {
                "date": SAMPLE_DATE_A,
                "hour": h,
                "temperature_c": 20.0,
                "precipitation_mm": 0.0,
                "weathercode": 0,
                "source": "archive",
            }
            for h in range(24)
        ] + [
            {
                "date": SAMPLE_DATE_C,
                "hour": h,
                "temperature_c": 22.0,
                "precipitation_mm": 0.0,
                "weathercode": 0,
                "source": "archive",
            }
            for h in range(24)
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=db_records)

        async def fake_fetch_weather(d, city="zurich"):
            return make_weather_df(d)

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather", side_effect=fake_fetch_weather
            ) as mock_http,
        ):
            result = await fetch_weather_batch(
                [SAMPLE_DATE_A, SAMPLE_DATE_B, SAMPLE_DATE_C]
            )

        # Only date B should trigger an HTTP call
        assert mock_http.call_count == 1
        assert mock_http.call_args[0][0] == SAMPLE_DATE_B

        # All 3 dates present in result
        assert len(result) == 72  # 3 × 24 rows

    async def test_nan_rows_not_persisted(self):
        """All-NaN successful fetch → returned to caller but not written to DB."""
        from ml.weather import _utc_today, fetch_weather_batch
        import numpy as np

        recent = _utc_today() - datetime.timedelta(days=1)
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # DB empty

        async def fake_fetch_weather_nan(d, city="zurich"):
            df = make_weather_df(d)
            df["temperature_c"] = np.nan
            df["precipitation_mm"] = np.nan
            df["weathercode"] = np.nan
            return df

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch("ml.weather.fetch_weather", side_effect=fake_fetch_weather_nan),
        ):
            result = await fetch_weather_batch([recent])

        # NaN rows returned to caller
        assert len(result) == 24
        assert result["temperature_c"].isna().all()

        # But NOT written to DB
        mock_conn.executemany.assert_not_called()

    async def test_in_memory_cache_bypasses_db_for_recent_dates(self):
        """Fresh in-lag mem cache (with cache timestamp) skips both DB and HTTP."""
        from ml.weather import (
            FORECAST_WEATHER_MAX_AGE,
            _cache,
            _cache_at,
            _utc_today,
            fetch_weather_batch,
        )

        recent = _utc_today() - datetime.timedelta(days=1)
        _cache[("zurich", recent)] = make_weather_df(recent)
        _cache_at[("zurich", recent)] = (
            datetime.datetime.now(datetime.timezone.utc) - FORECAST_WEATHER_MAX_AGE / 2
        )

        mock_conn = AsyncMock()

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch("ml.weather.fetch_weather") as mock_http,
        ):
            result = await fetch_weather_batch([recent])

        mock_conn.fetch.assert_not_called()
        mock_http.assert_not_called()
        assert len(result) == 24


class TestForecastRefreshPolicy:
    """In-lag forecast days re-fetch when fetched_at is missing or older than TTL."""

    @staticmethod
    def _db_day(date, *, temp=20.0, code=0, source="forecast", fetched_at=None):
        return [
            {
                "date": date,
                "hour": h,
                "temperature_c": temp,
                "precipitation_mm": 0.0,
                "weathercode": code,
                "source": source,
                "fetched_at": fetched_at,
            }
            for h in range(24)
        ]

    async def test_stale_in_lag_forecast_refetches_and_upserts(self):
        from ml.weather import (
            FORECAST_WEATHER_MAX_AGE,
            SOURCE_FORECAST,
            _utc_today,
            fetch_weather_batch,
        )

        recent = _utc_today() - datetime.timedelta(days=1)
        stale_at = datetime.datetime.now(datetime.timezone.utc) - (
            FORECAST_WEATHER_MAX_AGE + datetime.timedelta(minutes=1)
        )
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=self._db_day(
                recent, temp=21.5, code=1, source="forecast", fetched_at=stale_at
            )
        )
        fresh_df = make_weather_df(recent, temp=29.0)
        fresh_df.loc[fresh_df["hour"] == 12, "weathercode"] = 0

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                return_value=fresh_df,
            ) as mock_http,
        ):
            result = await fetch_weather_batch([recent])

        mock_http.assert_awaited()
        sql = mock_conn.executemany.call_args[0][0]
        assert "DO UPDATE" in sql.upper()
        records = mock_conn.executemany.call_args[0][1]
        assert all(r[7] == SOURCE_FORECAST for r in records)
        noon = result[result["hour"] == 12].iloc[0]
        assert noon["temperature_c"] == pytest.approx(29.0 + 12 * 0.1)

    async def test_fresh_in_lag_forecast_skips_http(self):
        from ml.weather import FORECAST_WEATHER_MAX_AGE, _utc_today, fetch_weather_batch

        recent = _utc_today() - datetime.timedelta(days=1)
        fresh_at = datetime.datetime.now(datetime.timezone.utc) - (
            FORECAST_WEATHER_MAX_AGE / 3
        )
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=self._db_day(
                recent, temp=22.0, code=0, source="forecast", fetched_at=fresh_at
            )
        )

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch("ml.weather.fetch_weather") as mock_http,
        ):
            result = await fetch_weather_batch([recent])

        mock_http.assert_not_called()
        mock_conn.executemany.assert_not_called()
        assert result[result["hour"] == 0].iloc[0]["temperature_c"] == pytest.approx(
            22.0
        )

    async def test_null_fetched_at_in_lag_triggers_refresh(self):
        """Legacy write-once forecast rows (null fetched_at) must refresh."""
        from ml.weather import _utc_today, fetch_weather_batch

        recent = _utc_today() - datetime.timedelta(days=2)
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=self._db_day(
                recent, temp=21.5, code=1, source="forecast", fetched_at=None
            )
        )

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                return_value=make_weather_df(recent, temp=27.0),
            ) as mock_http,
        ):
            await fetch_weather_batch([recent])

        mock_http.assert_awaited()


class TestArchiveBatchPolicy:
    """Archive-eligible days upgrade forecast/legacy rows; archive-backed skip HTTP."""

    @staticmethod
    def _db_day(date, *, temp=20.0, code=0, source="forecast"):
        return [
            {
                "date": date,
                "hour": h,
                "temperature_c": temp if h != 12 else temp,
                "precipitation_mm": 0.0,
                "weathercode": code if h != 12 else code,
                "source": source,
            }
            for h in range(24)
        ]

    async def test_eligible_forecast_source_triggers_archive_upsert(self):
        from ml.weather import fetch_weather_batch

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=self._db_day(
                SAMPLE_DATE_A, temp=24.7, code=1, source="forecast"
            )
        )
        archive_df = make_weather_df(SAMPLE_DATE_A, temp=20.0)
        archive_df.loc[archive_df["hour"] == 12, "temperature_c"] = 28.4
        archive_df.loc[archive_df["hour"] == 12, "weathercode"] = 0

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                return_value=archive_df,
            ) as mock_http,
        ):
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        mock_http.assert_awaited()
        mock_conn.executemany.assert_called()
        sql = mock_conn.executemany.call_args[0][0]
        assert "DO UPDATE" in sql.upper()
        noon = result[result["hour"] == 12].iloc[0]
        assert noon["temperature_c"] == pytest.approx(28.4)
        assert int(noon["weathercode"]) == 0

    async def test_eligible_archive_source_skips_http(self):
        from ml.weather import fetch_weather_batch

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=self._db_day(
                SAMPLE_DATE_A, temp=20.0, code=0, source="archive"
            )
        )

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch("ml.weather.fetch_weather") as mock_http,
        ):
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        mock_http.assert_not_called()
        assert len(result) == 24

    async def test_eligible_null_source_triggers_archive_refresh(self):
        from ml.weather import fetch_weather_batch

        rows = self._db_day(SAMPLE_DATE_A, temp=21.5, code=1, source=None)
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        archive_df = make_weather_df(SAMPLE_DATE_A, temp=28.4)

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                return_value=archive_df,
            ) as mock_http,
        ):
            await fetch_weather_batch([SAMPLE_DATE_A])

        mock_http.assert_awaited()

    async def test_stale_forecast_regression_upgrades_noon(self):
        """Frozen 24.7°C/code 1 becomes archive 28.4°C/code 0 after batch."""
        from ml.weather import fetch_weather_batch

        rows = self._db_day(SAMPLE_DATE_A, temp=24.7, code=1, source="forecast")
        for row in rows:
            if row["hour"] == 12:
                row["temperature_c"] = 24.7
                row["weathercode"] = 1
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)

        archive_df = make_weather_df(SAMPLE_DATE_A, temp=20.0)
        archive_df.loc[:, "temperature_c"] = 20.0
        archive_df.loc[archive_df["hour"] == 12, "temperature_c"] = 28.4
        archive_df.loc[archive_df["hour"] == 12, "weathercode"] = 0

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                return_value=archive_df,
            ),
        ):
            result = await fetch_weather_batch([SAMPLE_DATE_A])

        noon = result[result["hour"] == 12].iloc[0]
        assert noon["temperature_c"] == pytest.approx(28.4)
        assert int(noon["weathercode"]) == 0

    async def test_in_lag_date_uses_forecast_upsert_not_archive_source(self):
        from ml.weather import SOURCE_FORECAST, _utc_today, fetch_weather_batch

        recent = _utc_today() - datetime.timedelta(days=1)
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                return_value=make_weather_df(recent),
            ),
        ):
            await fetch_weather_batch([recent])

        sql = mock_conn.executemany.call_args[0][0]
        assert "DO UPDATE" in sql.upper()
        assert "ARCHIVE" in sql.upper()
        assert "LIVE" in sql.upper()
        records = mock_conn.executemany.call_args[0][1]
        assert all(r[7] == SOURCE_FORECAST for r in records)

    async def test_incomplete_archive_day_triggers_refresh(self):
        """Fewer than 24 archive hours must not count as archive-backed."""
        from ml.weather import fetch_weather_batch

        # Only 3 hours tagged archive — incomplete day
        rows = self._db_day(SAMPLE_DATE_A, temp=20.0, code=0, source="archive")[:3]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        archive_df = make_weather_df(SAMPLE_DATE_A, temp=28.0)

        with (
            patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)),
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                return_value=archive_df,
            ) as mock_http,
        ):
            await fetch_weather_batch([SAMPLE_DATE_A])

        mock_http.assert_awaited()

    async def test_forecast_persist_does_not_clobber_existing_archive_row(self):
        """Forecast upsert WHERE clause leaves archive (and live) values in place."""
        from ml.weather import SOURCE_ARCHIVE, SOURCE_LIVE, _persist_to_db

        store: dict[tuple, dict] = {
            ("zurich", SAMPLE_DATE_A, 12): {
                "temperature_c": 28.4,
                "precipitation_mm": 0.0,
                "weathercode": 0,
                "source": SOURCE_ARCHIVE,
            },
            ("zurich", SAMPLE_DATE_A, 13): {
                "temperature_c": 29.0,
                "precipitation_mm": 0.0,
                "weathercode": 0,
                "source": SOURCE_LIVE,
            },
        }

        async def executemany(sql, records):
            assert "DO UPDATE" in sql.upper()
            assert "ARCHIVE" in sql.upper()
            assert "LIVE" in sql.upper()
            for rec in records:
                city, date, hour, temp, precip, code, _fetched, source = rec
                key = (city, date, hour)
                existing = store.get(key)
                if existing and existing["source"] in (SOURCE_ARCHIVE, SOURCE_LIVE):
                    continue  # WHERE source IS DISTINCT FROM archive/live
                store[key] = {
                    "temperature_c": temp,
                    "precipitation_mm": precip,
                    "weathercode": code,
                    "source": source,
                }

        mock_conn = AsyncMock()
        mock_conn.executemany = AsyncMock(side_effect=executemany)
        forecast_df = make_weather_df(SAMPLE_DATE_A, temp=24.7)
        forecast_df.loc[forecast_df["hour"] == 12, "temperature_c"] = 24.7
        forecast_df.loc[forecast_df["hour"] == 12, "weathercode"] = 1
        forecast_df["date"] = SAMPLE_DATE_A

        await _persist_to_db(mock_conn, forecast_df, city="zurich")

        assert store[("zurich", SAMPLE_DATE_A, 12)]["temperature_c"] == pytest.approx(
            28.4
        )
        assert store[("zurich", SAMPLE_DATE_A, 12)]["source"] == SOURCE_ARCHIVE
        assert store[("zurich", SAMPLE_DATE_A, 13)]["temperature_c"] == pytest.approx(
            29.0
        )
        assert store[("zurich", SAMPLE_DATE_A, 13)]["source"] == SOURCE_LIVE
        assert store[("zurich", SAMPLE_DATE_A, 0)]["source"] == "forecast"

    async def test_archive_upsert_reuses_one_db_connection(self):
        """Archive loop must not open a new connection per dirty day."""
        from ml.weather import fetch_weather_batch

        dates = [SAMPLE_DATE_A, SAMPLE_DATE_B]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # both days missing
        connect = AsyncMock(return_value=mock_conn)

        with (
            patch("ml.weather._get_db_conn", connect),
            patch(
                "ml.weather.fetch_weather",
                new_callable=AsyncMock,
                side_effect=lambda d, city="zurich": make_weather_df(d),
            ),
        ):
            await fetch_weather_batch(dates)

        # One connect for initial SELECT + one for the archive upsert loop
        # (not one connect per dirty date inside the loop).
        assert connect.await_count == 2
        mock_conn.close.assert_awaited()


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
