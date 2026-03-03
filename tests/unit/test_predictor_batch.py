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


# --- Test: model.predict called exactly once (batch, not 24×) ---
async def test_predict_range_batch_single_model_call():
    """model.predict must be called once with a 24-row matrix, not 24 times."""
    predictor = Predictor()
    predictor.model = MagicMock()
    predictor.model.predict = MagicMock(return_value=[50.0] * 24)
    predictor._encoding_map = {}

    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]

    with patch.object(predictor, '_reload_if_stale'):
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            mock_lag.return_value = ([None] * 24, [None] * 24)
            await predictor.predict_range_batch("fb001", hours, db_pool=None)

    assert predictor.model.predict.call_count == 1
    call_args = predictor.model.predict.call_args[0][0]
    assert call_args.shape[0] == 24, f"Expected 24 rows, got {call_args.shape[0]}"


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
    predictor.model.predict = MagicMock(return_value=[50.0] * 24)
    predictor._encoding_map = {}

    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]
    mock_pool = MagicMock()

    with patch.object(predictor, '_reload_if_stale'):
        with patch.object(predictor, '_fetch_lag_features_batch', new_callable=AsyncMock) as mock_lag:
            mock_lag.return_value = ([None] * 24, [None] * 24)
            await predictor.predict_range_batch("fb001", hours, db_pool=mock_pool)

    mock_lag.assert_called_once_with(mock_pool, "fb001", hours)


# --- Test: _fetch_lag_features_batch graceful fallback when db_pool=None ---
async def test_fetch_lag_features_batch_no_pool_returns_nones():
    """When db_pool is None, should return (list of None, list of None)."""
    predictor = Predictor()
    hours = [datetime(2026, 3, 3, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]
    lag_1h, lag_1w = await predictor._fetch_lag_features_batch(None, "fb001", hours)
    assert lag_1h == [None] * 24
    assert lag_1w == [None] * 24
