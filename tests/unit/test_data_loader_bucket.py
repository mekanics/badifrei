"""TDD tests for TASK-028: configurable time-bucket downsampling in data loader."""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pandas as pd
import pytest

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 2, 1, tzinfo=timezone.utc)


def make_raw_rows(n=1000, pool_uid="SSD-5"):
    """Create n fake asyncpg rows (raw granularity)."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        t = base + timedelta(hours=i)
        rows.append({
            "time": t,
            "pool_uid": pool_uid,
            "pool_name": "Kaeferberg",
            "current_fill": i % 70,
            "max_space": 70,
            "free_space": 70 - (i % 70),
            "occupancy_pct": (i % 70) / 70 * 100,
        })
    return rows


def make_bucketed_rows(n_buckets=6, pool_uid="SSD-5"):
    """Simulate what TimescaleDB returns after time_bucket aggregation.

    1 hour of data @ 10-min buckets → 6 rows per pool.
    """
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n_buckets):
        t = base + timedelta(minutes=10 * i)
        rows.append({
            "time": t,
            "pool_uid": pool_uid,
            "pool_name": "Kaeferberg",
            "current_fill": 30 + i,
            "max_space": 70,
            "free_space": 40 - i,
            "occupancy_pct": (30 + i) / 70 * 100,
        })
    return rows


def patch_db(rows):
    """Return (context_manager, mock_conn) patching asyncpg.connect."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)
    mock_conn.close = AsyncMock()
    ctx = patch("ml.data_loader.asyncpg.connect", AsyncMock(return_value=mock_conn))
    return ctx, mock_conn


# ---------------------------------------------------------------------------
# Test 1 — bucketed query uses time_bucket and AVG
# ---------------------------------------------------------------------------

class TestBucketedQueryUsesTimeBucket:
    async def test_bucketed_query_uses_time_bucket(self):
        """SQL generated for bucket_interval='10 minutes' must contain time_bucket and AVG(."""
        from ml.data_loader import load_data

        rows = make_raw_rows(1000)
        ctx, mock_conn = patch_db(rows)

        with ctx:
            await load_data(START, END, min_records=100, bucket_interval="10 minutes")

        sql_called = mock_conn.fetch.call_args[0][0]
        assert "time_bucket" in sql_called, f"Expected 'time_bucket' in SQL, got:\n{sql_called}"
        assert "AVG(" in sql_called, f"Expected 'AVG(' in SQL, got:\n{sql_called}"

    async def test_bucketed_query_aliases_bucket_to_time(self):
        """Bucketed query must alias the bucket column as 'time' for pipeline compat."""
        from ml.data_loader import load_data

        rows = make_raw_rows(1000)
        ctx, mock_conn = patch_db(rows)

        with ctx:
            await load_data(START, END, min_records=100, bucket_interval="10 minutes")

        sql_called = mock_conn.fetch.call_args[0][0]
        # Should see the alias pattern
        assert "AS time" in sql_called or "as time" in sql_called.lower(), (
            f"Expected bucket alias 'AS time' in SQL, got:\n{sql_called}"
        )

    async def test_bucketed_query_interval_is_parameterised(self):
        """Interval must be passed as a query parameter, NOT interpolated via f-string."""
        from ml.data_loader import load_data

        rows = make_raw_rows(1000)
        ctx, mock_conn = patch_db(rows)

        interval = "10 minutes"
        with ctx:
            await load_data(START, END, min_records=100, bucket_interval=interval)

        call_args_positional = mock_conn.fetch.call_args[0]
        # The interval string should appear as a parameter, not in the SQL template
        sql = call_args_positional[0]
        assert interval not in sql, (
            f"Interval '{interval}' must NOT be f-string interpolated into SQL "
            f"(SQL injection risk). Got:\n{sql}"
        )
        # The interval should be in the positional params
        assert interval in call_args_positional[1:], (
            f"Interval '{interval}' must appear as a positional query parameter. "
            f"Got params: {call_args_positional[1:]}"
        )


# ---------------------------------------------------------------------------
# Test 2 — raw query unchanged when bucket_interval=None
# ---------------------------------------------------------------------------

class TestRawQueryUnchanged:
    async def test_raw_query_unchanged(self):
        """bucket_interval=None → raw SELECT * path, no time_bucket in SQL."""
        from ml.data_loader import load_data

        rows = make_raw_rows(1000)
        ctx, mock_conn = patch_db(rows)

        with ctx:
            await load_data(START, END, min_records=100, bucket_interval=None)

        sql_called = mock_conn.fetch.call_args[0][0]
        assert "time_bucket" not in sql_called, (
            f"Raw path must not use time_bucket, but got:\n{sql_called}"
        )

    async def test_raw_query_still_returns_all_columns(self):
        """Raw path must still return the expected columns."""
        from ml.data_loader import load_data

        rows = make_raw_rows(1000)
        ctx, _ = patch_db(rows)

        with ctx:
            df = await load_data(START, END, min_records=100, bucket_interval=None)

        for col in ["time", "pool_uid", "pool_name", "current_fill", "max_space", "free_space", "occupancy_pct"]:
            assert col in df.columns


# ---------------------------------------------------------------------------
# Test 3 — empty string falls back to raw
# ---------------------------------------------------------------------------

class TestEmptyBucketIntervalFallsBackToRaw:
    async def test_empty_bucket_interval_falls_back_to_raw(self):
        """bucket_interval='' → raw path (no time_bucket)."""
        from ml.data_loader import load_data

        rows = make_raw_rows(1000)
        ctx, mock_conn = patch_db(rows)

        with ctx:
            await load_data(START, END, min_records=100, bucket_interval="")

        sql_called = mock_conn.fetch.call_args[0][0]
        assert "time_bucket" not in sql_called, (
            f"Empty string bucket_interval must fall back to raw path, got:\n{sql_called}"
        )


# ---------------------------------------------------------------------------
# Test 4 — TRAINING_BUCKET_INTERVAL env var is picked up by retrain.py
# ---------------------------------------------------------------------------

class TestEnvVarPickedUp:
    async def test_env_var_picked_up_by_retrain(self, monkeypatch):
        """retrain_job() forwards TRAINING_BUCKET_INTERVAL env var to load_data()."""
        monkeypatch.setenv("TRAINING_BUCKET_INTERVAL", "5 minutes")

        # We need to import retrain AFTER setting the env var (runtime reading)
        from ml.retrain import retrain_job

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        df = pd.DataFrame([{
            "time": base + timedelta(hours=i),
            "pool_uid": "SSD-1",
            "pool_name": "Pool",
            "current_fill": 30,
            "max_space": 100,
            "free_space": 70,
            "occupancy_pct": 30.0,
        } for i in range(1500)])

        mock_model = MagicMock()
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True
        mock_report.worst_pool = "SSD-1"
        mock_report.per_pool = [MagicMock(mae=5.0)]

        load_data_mock = AsyncMock(return_value=df)

        with patch("ml.retrain.load_data", load_data_mock), \
             patch("ml.retrain.train", return_value=(mock_model, {"mae": 5.0})), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("ml.retrain.evaluate", return_value=mock_report), \
             patch("ml.retrain._prune_old_models"), \
             patch("ml.retrain._fetch_weather_for_df", AsyncMock(return_value=None)):
            await retrain_job()

        load_data_mock.assert_called_once()
        kwargs = load_data_mock.call_args[1]
        assert kwargs.get("bucket_interval") == "5 minutes", (
            f"retrain_job() should forward TRAINING_BUCKET_INTERVAL='5 minutes' "
            f"as bucket_interval kwarg, got: {kwargs}"
        )

    async def test_env_var_picked_up_by_train_script(self, monkeypatch):
        """scripts/train.py forwards TRAINING_BUCKET_INTERVAL env var to load_data()."""
        monkeypatch.setenv("TRAINING_BUCKET_INTERVAL", "5 minutes")

        # Import the main function from scripts/train
        import importlib
        import scripts.train as train_script
        importlib.reload(train_script)

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        df = pd.DataFrame([{
            "time": base + timedelta(hours=i),
            "pool_uid": "SSD-1",
            "pool_name": "Pool",
            "current_fill": 30,
            "max_space": 100,
            "free_space": 70,
            "occupancy_pct": 30.0,
        } for i in range(1500)])

        mock_model = MagicMock()
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.model_rmse = 7.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True
        mock_report.worst_pool = "SSD-1"
        mock_report.per_pool = [MagicMock(mae=5.0)]

        load_data_mock = AsyncMock(return_value=df)

        with patch("scripts.train.load_data", load_data_mock), \
             patch("scripts.train.train", return_value=(mock_model, {"mae": 5.0})), \
             patch("scripts.train.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("scripts.train.evaluate", return_value=mock_report), \
             patch("scripts.train.time_based_split", return_value=(df, df)), \
             patch("ml.retrain._fetch_weather_for_df", AsyncMock(return_value=None)):
            await train_script.main()

        load_data_mock.assert_called_once()
        kwargs = load_data_mock.call_args[1]
        assert kwargs.get("bucket_interval") == "5 minutes", (
            f"scripts/train.py should forward TRAINING_BUCKET_INTERVAL='5 minutes' "
            f"as bucket_interval kwarg, got: {kwargs}"
        )


# ---------------------------------------------------------------------------
# Test 5 — bucketed record count plausible (integration-style with mock DB)
# ---------------------------------------------------------------------------

class TestBucketedRecordCountPlausible:
    async def test_bucketed_record_count_plausible(self):
        """
        Integration-style: DB returns 6 pre-bucketed rows for 1 pool (simulating
        TimescaleDB time_bucket over 1 hour @ 10-min intervals).
        load_data() should return ≤ 10 rows and > 0.
        """
        from ml.data_loader import load_data

        # Simulate what TimescaleDB returns: 6 buckets per pool for 1 hour
        bucketed_rows = make_bucketed_rows(n_buckets=6, pool_uid="SSD-5")
        ctx, _ = patch_db(bucketed_rows)

        with ctx:
            df = await load_data(
                START, END,
                min_records=1,  # Low threshold for test
                bucket_interval="10 minutes",
            )

        assert len(df) > 0, "Expected at least one row"
        assert len(df) <= 10, (
            f"Expected ≤ 10 rows (6 buckets × 1 pool ± rounding), got {len(df)}"
        )

    async def test_bucketed_columns_present(self):
        """Bucketed result must still have the standard 7 columns."""
        from ml.data_loader import load_data

        bucketed_rows = make_bucketed_rows(n_buckets=6)
        ctx, _ = patch_db(bucketed_rows)

        with ctx:
            df = await load_data(START, END, min_records=1, bucket_interval="10 minutes")

        for col in ["time", "pool_uid", "pool_name", "current_fill", "max_space", "free_space", "occupancy_pct"]:
            assert col in df.columns, f"Missing column: {col}"

    async def test_default_bucket_interval_is_ten_minutes(self):
        """DEFAULT_BUCKET_INTERVAL constant must be '10 minutes'."""
        from ml.data_loader import DEFAULT_BUCKET_INTERVAL
        assert DEFAULT_BUCKET_INTERVAL == "10 minutes"
