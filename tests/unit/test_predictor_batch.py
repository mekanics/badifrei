"""Tests for Predictor.predict_range_batch() — TDD."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from api.predictor import Predictor


# --- Test: predict_range_batch returns 24 predictions ---
async def test_predict_range_batch_returns_24_values():
    """Should return exactly 24 float predictions, one per hour."""
    predictor = Predictor()
    predictor.model = MagicMock()
    predictor.model.predict = MagicMock(return_value=[50.0] * 24)
    predictor._encoding_map = {}

    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]

    with patch.object(predictor, '_reload_if_stale'):
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            mock_lag.return_value = ([None] * 24, [None] * 24)
            result = await predictor.predict_range_batch("fb001", hours, db_pool=None)

    assert len(result) == 24
    assert all(isinstance(v, float) for v in result)


# --- Test: predictions are clipped to [0, 100] ---
async def test_predict_range_batch_clips_values():
    """XGBoost can output values outside [0, 100]; must be clipped."""
    predictor = Predictor()
    predictor.model = MagicMock()
    predictor.model.predict = MagicMock(return_value=[-10.0] * 12 + [110.0] * 12)
    predictor._encoding_map = {}

    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]

    with patch.object(predictor, '_reload_if_stale'):
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            mock_lag.return_value = ([None] * 24, [None] * 24)
            result = await predictor.predict_range_batch("fb001", hours, db_pool=None)

    assert all(0.0 <= v <= 100.0 for v in result), f"Out-of-range values: {result}"


# --- Test: _reload_if_stale called exactly once ---
async def test_predict_range_batch_reloads_once():
    """_reload_if_stale must be called exactly once, not 24 times."""
    predictor = Predictor()
    predictor.model = MagicMock()
    predictor.model.predict = MagicMock(return_value=[50.0] * 24)
    predictor._encoding_map = {}

    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]

    with patch.object(predictor, '_reload_if_stale') as mock_reload:
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            mock_lag.return_value = ([None] * 24, [None] * 24)
            await predictor.predict_range_batch("fb001", hours, db_pool=None)

    mock_reload.assert_called_once()


# --- Test: model.predict called once per hour (recursive forecasting loop) ---
async def test_predict_range_batch_single_model_call():
    """model.predict is called once per hour (recursive forecasting — each hour feeds into the next).

    Note: predict is called 24× (one per row) rather than once with a batch matrix
    because lag_1h for future hours must use the *previous* prediction, making
    fully-batched inference impossible.
    """
    predictor = Predictor()
    predictor.model = MagicMock()
    predictor.model.predict = MagicMock(return_value=[50.0])
    predictor._encoding_map = {}

    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]

    with patch.object(predictor, '_reload_if_stale'):
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            with patch.object(predictor, '_fetch_rolling_mean_7d', new_callable=AsyncMock) as mock_rmean:
                with patch.object(predictor, '_fetch_weather_multi_date_safe', new_callable=AsyncMock) as mock_wx:
                    mock_lag.return_value = ([None] * 24, [None] * 24)
                    mock_rmean.return_value = None
                    mock_wx.return_value = None
                    await predictor.predict_range_batch("fb001", hours, db_pool=None)

    # One predict call per hour (recursive forecasting — each hour feeds the next)
    assert predictor.model.predict.call_count == 24


# --- Test: when model not loaded, returns zeros ---
async def test_predict_range_batch_no_model_returns_zeros():
    """When model is not loaded, must return 24 zeros without crashing."""
    predictor = Predictor()
    # model is None by default

    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]
    result = await predictor.predict_range_batch("fb001", hours, db_pool=None)

    assert result == [0.0] * 24


# --- Test: _fetch_lag_features_batch called once with all 24 hours ---
async def test_fetch_lag_features_batch_called_once():
    """Lag features must be fetched in one batch call, not 24 separate calls."""
    predictor = Predictor()
    predictor.model = MagicMock()
    predictor.model.predict = MagicMock(return_value=[50.0])
    predictor._encoding_map = {}

    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]
    mock_pool = AsyncMock()

    with patch.object(predictor, '_reload_if_stale'):
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            with patch.object(predictor, '_fetch_rolling_mean_7d', new_callable=AsyncMock) as mock_rmean:
                with patch.object(predictor, '_fetch_weather_multi_date_safe', new_callable=AsyncMock) as mock_wx:
                    mock_lag.return_value = ([None] * 24, [None] * 24)
                    mock_rmean.return_value = None
                    mock_wx.return_value = None
                    await predictor.predict_range_batch("fb001", hours, db_pool=mock_pool)

    mock_lag.assert_called_once_with(mock_pool, "fb001", hours)


# --- Test: timezone key mismatch doesn't silently zero out lag features ---
async def test_fetch_lag_features_batch_returns_actual_values():
    """Lag values from mock DB should populate result, not silently degrade to None.

    Regression test for timezone key mismatch: asyncpg returns timezone-aware
    datetimes; the hours list uses timezone.utc. Keys must normalize correctly.
    """
    from unittest.mock import MagicMock
    import asyncio

    predictor = Predictor()
    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]

    # Simulate asyncpg returning timezone-aware datetimes (as asyncpg does for timestamptz)
    # asyncpg returns datetime with tzinfo=datetime.timezone.utc
    mock_pool = MagicMock()

    # Build mock records that match hours[0] = 2026-03-03 00:00 UTC
    mock_record_recent = {"target_time": datetime(2026, 3, 3, 0, 0, 0, tzinfo=timezone.utc), "occupancy_pct": 42.0}
    mock_record_week = {"target_time": datetime(2026, 2, 24, 0, 0, 0, tzinfo=timezone.utc), "occupancy_pct": 37.0}

    async def mock_fetch(sql, *args):
        # Return one record for first hour, None for the rest
        if args[0][0] == hours[0]:  # recent query
            return [mock_record_recent] + [{"target_time": h, "occupancy_pct": None} for h in args[0][1:]]
        else:  # week_ago query
            return [mock_record_week] + [{"target_time": h, "occupancy_pct": None} for h in args[0][1:]]

    mock_pool.fetch = mock_fetch

    lag_1h, lag_1w = await predictor._fetch_lag_features_batch(mock_pool, "fb001", hours)

    # First hour should have actual values, not None
    assert lag_1h[0] == 42.0, f"Expected 42.0, got {lag_1h[0]} — possible timezone key mismatch"
    assert lag_1w[0] == 37.0, f"Expected 37.0, got {lag_1w[0]} — possible timezone key mismatch"
    # Rest should be None (no data)
    assert lag_1h[1] is None
    assert lag_1w[1] is None


# --- Test: _fetch_lag_features_batch graceful fallback when db_pool=None ---
async def test_fetch_lag_features_batch_no_pool_returns_nones():
    """When db_pool is None, should return (list of None, list of None)."""
    predictor = Predictor()
    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]
    lag_1h, lag_1w = await predictor._fetch_lag_features_batch(None, "fb001", hours)
    assert lag_1h == [None] * 24
    assert lag_1w == [None] * 24


# =============================================================================
# Fix 2: Multi-date weather fetch tests
# =============================================================================

# --- Test: predict_range_batch collects all unique dates for weather fetch ---
async def test_predict_range_batch_passes_all_unique_dates_to_weather():
    """For a 168-hour (7-day) call, weather must be fetched for all 7 dates, not just day 1.

    Regression for: predict_range_batch only fetching first-date weather, causing
    days 2–7 to silently use Monday's weather for all predictions.
    """
    from datetime import date as date_type

    predictor = Predictor()
    predictor.model = MagicMock()
    predictor.model.predict = MagicMock(return_value=[50.0])
    predictor._encoding_map = {}

    # 7 days × 24 hours starting on Monday 2026-03-09
    from datetime import timedelta as td
    mon_date = datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)
    flat_hours = [
        datetime(
            (mon_date + td(days=d)).year,
            (mon_date + td(days=d)).month,
            (mon_date + td(days=d)).day,
            h, 0, 0, tzinfo=timezone.utc,
        )
        for d in range(7)
        for h in range(24)
    ]
    assert len(flat_hours) == 168

    with patch.object(predictor, '_reload_if_stale'):
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            with patch.object(predictor, '_fetch_rolling_mean_7d', new_callable=AsyncMock) as mock_rmean:
                with patch.object(predictor, '_fetch_weather_multi_date_safe', new_callable=AsyncMock) as mock_wx:
                    mock_lag.return_value = ([None] * 168, [None] * 168)
                    mock_rmean.return_value = None
                    mock_wx.return_value = None
                    await predictor.predict_range_batch("fb001", flat_hours, db_pool=None)

    # Must be called with all 7 unique dates, not just the first one
    call_args = mock_wx.call_args
    called_dates, called_city = call_args[0]
    assert len(called_dates) == 7, (
        f"Expected 7 unique dates, got {len(called_dates)}. "
        "predict_range_batch is likely only fetching the first date."
    )
    expected_dates = sorted({h.date() for h in flat_hours})
    assert sorted(called_dates) == expected_dates


# --- Test: predict_range_batch uses pool's city when fetching weather ---
async def test_predict_range_batch_uses_pool_city_for_weather():
    """Weather must be fetched for the pool's own city slug, not always 'zurich'."""
    predictor = Predictor()
    predictor.model = MagicMock()
    predictor.model.predict = MagicMock(return_value=[50.0])
    predictor._encoding_map = {}
    # Inject metadata with a non-default city
    predictor._metadata = {"luzern001": {"uid": "luzern001", "city": "luzern", "name": "Test Pool"}}

    hours = [datetime(2026, 3, 9, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]

    with patch.object(predictor, '_reload_if_stale'):
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            with patch.object(predictor, '_fetch_rolling_mean_7d', new_callable=AsyncMock) as mock_rmean:
                with patch.object(predictor, '_fetch_weather_multi_date_safe', new_callable=AsyncMock) as mock_wx:
                    mock_lag.return_value = ([None] * 24, [None] * 24)
                    mock_rmean.return_value = None
                    mock_wx.return_value = None
                    await predictor.predict_range_batch("luzern001", hours, db_pool=None)

    called_dates, called_city = mock_wx.call_args[0]
    assert called_city == "luzern", (
        f"Expected city='luzern', got '{called_city}'. "
        "predict_range_batch must look up the pool's city from metadata."
    )


# --- Test: _fetch_weather_multi_date_safe adds city column ---
async def test_fetch_weather_multi_date_safe_adds_city_column():
    """The returned DataFrame must include a 'city' column for the city-aware join path."""
    import pandas as pd
    from datetime import date

    predictor = Predictor()

    mock_df = pd.DataFrame({
        "date": [date(2026, 3, 9)] * 24,
        "hour": list(range(24)),
        "temperature_c": [15.0] * 24,
        "precipitation_mm": [0.0] * 24,
        "weathercode": [0] * 24,
    })

    with patch("ml.weather.fetch_weather_batch", new_callable=AsyncMock) as mock_batch:
        mock_batch.return_value = mock_df
        result = await predictor._fetch_weather_multi_date_safe([date(2026, 3, 9)], "zurich")

    assert result is not None
    assert "city" in result.columns, "weather DataFrame must have a 'city' column"
    assert (result["city"] == "zurich").all()


# --- Test: _fetch_weather_multi_date_safe returns None for empty date list ---
async def test_fetch_weather_multi_date_safe_empty_dates_returns_none():
    """Should return None immediately when no dates are provided."""
    predictor = Predictor()
    result = await predictor._fetch_weather_multi_date_safe([], "zurich")
    assert result is None


# --- Test: _fetch_weather_multi_date_safe returns None on exception ---
async def test_fetch_weather_multi_date_safe_handles_exception():
    """Should catch exceptions from fetch_weather_batch and return None gracefully."""
    from datetime import date

    predictor = Predictor()

    with patch("ml.weather.fetch_weather_batch", new_callable=AsyncMock) as mock_batch:
        mock_batch.side_effect = RuntimeError("network error")
        result = await predictor._fetch_weather_multi_date_safe([date(2026, 3, 9)], "zurich")

    assert result is None
