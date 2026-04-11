"""Tests for ml.lag_policy inference helpers."""

import pytest


class TestResolveLag1h:
    def test_db_lag_wins_when_present(self):
        from ml.lag_policy import resolve_lag_1h_for_inference

        v = resolve_lag_1h_for_inference(42.0, 10.0, 20.0, last_pred=99.0)
        assert v == 42.0

    def test_cold_start_prefers_week_over_rolling(self):
        from ml.lag_policy import resolve_lag_1h_for_inference

        v = resolve_lag_1h_for_inference(None, 40.0, 20.0, None)
        assert 25 < v < 35  # 0.55*40 + 0.45*20 = 31

    def test_recursion_blends_last_pred(self):
        from ml.lag_policy import resolve_lag_1h_for_inference

        anchor = 0.55 * 50.0 + 0.45 * 10.0
        v = resolve_lag_1h_for_inference(None, 50.0, 10.0, last_pred=20.0)
        assert v == pytest.approx(0.42 * 20.0 + 0.58 * anchor)

    def test_only_rolling_when_no_week(self):
        from ml.lag_policy import resolve_lag_1h_for_inference

        v = resolve_lag_1h_for_inference(None, None, 25.0, None)
        assert v == 25.0
