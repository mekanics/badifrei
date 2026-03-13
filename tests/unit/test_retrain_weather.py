"""Unit tests for TASK-024: retrain_job fetches and passes weather features.

No live DB or HTTP — all dependencies mocked.
"""
import datetime
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import numpy as np
import pandas as pd
import pytest
from datetime import timezone, timedelta


def make_df(n: int = 1500) -> pd.DataFrame:
    """Minimal occupancy DataFrame with 'time' column spanning multiple dates."""
    base = datetime.datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = []
    for i in range(n):
        records.append(
            {
                "time": base + timedelta(hours=i),
                "pool_uid": f"SSD-{(i % 3) + 1}",
                "pool_name": "Pool",
                "current_fill": 30 + (i % 40),
                "max_space": 100,
                "free_space": 70 - (i % 40),
                "occupancy_pct": float(30 + (i % 40)),
            }
        )
    return pd.DataFrame(records)


def make_weather_df(dates) -> pd.DataFrame:
    """Minimal weather DataFrame for a list of dates."""
    rows = []
    for d in dates:
        for h in range(24):
            rows.append(
                {
                    "date": d,
                    "hour": h,
                    "temperature_c": 20.0,
                    "precipitation_mm": 0.0,
                    "weathercode": 0,
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def clear_weather_cache():
    from ml.weather import clear_cache
    clear_cache()
    yield
    clear_cache()


class TestRetrainJobFetchesWeather:
    """retrain_job should call fetch_weather_batch and pass result to train()."""

    async def test_retrain_job_calls_fetch_weather_batch(self):
        """fetch_weather_batch is called with the unique dates from the loaded df."""
        from ml.retrain import retrain_job

        df = make_df(1500)
        expected_dates = pd.to_datetime(df["time"]).dt.date.unique()

        mock_model = MagicMock()
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True

        weather_df = make_weather_df(expected_dates)
        mock_fetch = AsyncMock(return_value=weather_df)

        with patch("ml.retrain.load_data", AsyncMock(return_value=df)), \
             patch("ml.retrain.fetch_weather_batch", mock_fetch), \
             patch("ml.retrain.train", return_value=(mock_model, {"mae": 5.0})), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("ml.retrain.evaluate", return_value=mock_report), \
             patch("ml.retrain._prune_old_models"):
            await retrain_job()

        mock_fetch.assert_called_once()
        called_dates = set(mock_fetch.call_args[0][0])
        assert called_dates == set(expected_dates)

    async def test_retrain_job_passes_weather_df_to_train(self):
        """train() is called with weather_df= equal to fetch_weather_batch's return value."""
        from ml.retrain import retrain_job

        df = make_df(1500)
        unique_dates = pd.to_datetime(df["time"]).dt.date.unique()
        weather_df = make_weather_df(unique_dates)

        mock_model = MagicMock()
        mock_train = MagicMock(return_value=(mock_model, {"mae": 5.0}))
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True

        with patch("ml.retrain.load_data", AsyncMock(return_value=df)), \
             patch("ml.retrain.fetch_weather_batch", AsyncMock(return_value=weather_df)), \
             patch("ml.retrain.train", mock_train), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("ml.retrain.evaluate", return_value=mock_report), \
             patch("ml.retrain._prune_old_models"):
            await retrain_job()

        mock_train.assert_called_once()
        _, kwargs = mock_train.call_args
        assert "weather_df" in kwargs
        assert kwargs["weather_df"] is weather_df

    async def test_retrain_job_continues_without_weather_on_fetch_failure(self, caplog):
        """fetch_weather_batch raises → warning logged, train called with weather_df=None."""
        from ml.retrain import retrain_job

        df = make_df(1500)

        mock_model = MagicMock()
        mock_train = MagicMock(return_value=(mock_model, {"mae": 5.0}))
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True

        with patch("ml.retrain.load_data", AsyncMock(return_value=df)), \
             patch("ml.retrain.fetch_weather_batch", AsyncMock(side_effect=Exception("network error"))), \
             patch("ml.retrain.train", mock_train), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("ml.retrain.evaluate", return_value=mock_report), \
             patch("ml.retrain._prune_old_models"), \
             caplog.at_level(logging.WARNING, logger="ml.retrain"):
            await retrain_job()

        # Must NOT raise
        mock_train.assert_called_once()
        _, kwargs = mock_train.call_args
        assert kwargs.get("weather_df") is None

        # Warning must mention "weather"
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("weather" in str(m).lower() for m in warning_messages)

    async def test_retrain_job_continues_without_weather_on_partial_nan(self):
        """NaN weather DataFrame → train() still called (NaN handled downstream)."""
        from ml.retrain import retrain_job

        df = make_df(1500)
        unique_dates = pd.to_datetime(df["time"]).dt.date.unique()
        nan_weather = pd.DataFrame(
            {
                "date": [d for d in unique_dates for _ in range(24)],
                "hour": list(range(24)) * len(unique_dates),
                "temperature_c": [np.nan] * (len(unique_dates) * 24),
                "precipitation_mm": [np.nan] * (len(unique_dates) * 24),
                "weathercode": [np.nan] * (len(unique_dates) * 24),
            }
        )

        mock_model = MagicMock()
        mock_train = MagicMock(return_value=(mock_model, {"mae": 5.0}))
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True

        with patch("ml.retrain.load_data", AsyncMock(return_value=df)), \
             patch("ml.retrain.fetch_weather_batch", AsyncMock(return_value=nan_weather)), \
             patch("ml.retrain.train", mock_train), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("ml.retrain.evaluate", return_value=mock_report), \
             patch("ml.retrain._prune_old_models"):
            await retrain_job()  # must not raise

        mock_train.assert_called_once()

    async def test_retrain_job_weather_dates_match_training_set(self):
        """fetch_weather_batch receives exactly the unique dates from df['time']."""
        from ml.retrain import retrain_job

        df = make_df(1500)
        expected = set(pd.to_datetime(df["time"]).dt.date.unique())

        mock_model = MagicMock()
        mock_fetch = AsyncMock(return_value=pd.DataFrame(
            columns=["date", "hour", "temperature_c", "precipitation_mm", "weathercode"]
        ))
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True

        with patch("ml.retrain.load_data", AsyncMock(return_value=df)), \
             patch("ml.retrain.fetch_weather_batch", mock_fetch), \
             patch("ml.retrain.train", return_value=(mock_model, {"mae": 5.0})), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("ml.retrain.evaluate", return_value=mock_report), \
             patch("ml.retrain._prune_old_models"):
            await retrain_job()

        passed_dates = set(mock_fetch.call_args[0][0])
        assert passed_dates == expected


class TestFetchWeatherForDfHelper:
    """_fetch_weather_for_df shared helper extracts dates and calls fetch_weather_batch."""

    async def test_helper_extracts_correct_dates(self):
        """_fetch_weather_for_df passes unique dates from df to fetch_weather_batch."""
        from ml.retrain import _fetch_weather_for_df

        df = make_df(1500)
        expected_dates = set(pd.to_datetime(df["time"]).dt.date.unique())

        mock_fetch = AsyncMock(return_value=pd.DataFrame(
            columns=["date", "hour", "temperature_c", "precipitation_mm", "weathercode"]
        ))

        with patch("ml.retrain.fetch_weather_batch", mock_fetch):
            await _fetch_weather_for_df(df)

        passed_dates = set(mock_fetch.call_args[0][0])
        assert passed_dates == expected_dates

    async def test_helper_returns_none_on_exception(self, caplog):
        """If fetch_weather_batch raises, helper logs warning and returns None."""
        from ml.retrain import _fetch_weather_for_df

        df = make_df(100)

        with patch("ml.retrain.fetch_weather_batch", AsyncMock(side_effect=Exception("boom"))), \
             caplog.at_level(logging.WARNING, logger="ml.retrain"):
            result = await _fetch_weather_for_df(df)

        assert result is None
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("weather" in str(m).lower() for m in warning_messages)
