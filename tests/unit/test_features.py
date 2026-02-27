"""Unit tests for feature engineering module."""
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest


def make_df(n=48, pool_uid="SSD-5", start="2026-02-01"):
    """Create a minimal test DataFrame."""
    times = pd.date_range(start=start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "time": times,
        "pool_uid": pool_uid,
        "occupancy_pct": [float(i % 100) for i in range(n)],
    })


class TestTimeFeatures:
    def test_adds_hour_of_day(self):
        from ml.features import add_time_features
        df = make_df()
        result = add_time_features(df)
        assert "hour_of_day" in result.columns
        assert result["hour_of_day"].between(0, 23).all()

    def test_adds_day_of_week(self):
        from ml.features import add_time_features
        df = make_df()
        result = add_time_features(df)
        assert "day_of_week" in result.columns
        assert result["day_of_week"].between(0, 6).all()

    def test_weekend_flag(self):
        from ml.features import add_time_features
        # 2026-02-07 is a Saturday
        df = pd.DataFrame({"time": ["2026-02-07 10:00:00+00:00", "2026-02-09 10:00:00+00:00"],
                           "pool_uid": "SSD-5", "occupancy_pct": [50.0, 30.0]})
        result = add_time_features(df)
        assert result.iloc[0]["is_weekend"] == 1  # Saturday
        assert result.iloc[1]["is_weekend"] == 0  # Monday

    def test_adds_month(self):
        from ml.features import add_time_features
        df = make_df(start="2026-02-01")
        result = add_time_features(df)
        assert result["month"].iloc[0] == 2

    def test_adds_day_of_year(self):
        from ml.features import add_time_features
        df = make_df(start="2026-01-01")
        result = add_time_features(df)
        assert result["day_of_year"].iloc[0] == 1

    def test_does_not_mutate_input(self):
        from ml.features import add_time_features
        df = make_df()
        original_cols = list(df.columns)
        add_time_features(df)
        assert list(df.columns) == original_cols


class TestHolidayFeature:
    def test_new_year_is_holiday(self):
        from ml.features import add_holiday_feature
        df = pd.DataFrame({"time": ["2026-01-01 10:00:00+00:00"],
                           "pool_uid": "SSD-5", "occupancy_pct": [50.0]})
        result = add_holiday_feature(df)
        assert result.iloc[0]["is_holiday"] == 1

    def test_regular_day_not_holiday(self):
        from ml.features import add_holiday_feature
        df = pd.DataFrame({"time": ["2026-02-27 10:00:00+00:00"],
                           "pool_uid": "SSD-5", "occupancy_pct": [50.0]})
        result = add_holiday_feature(df)
        assert result.iloc[0]["is_holiday"] == 0

    def test_returns_integer_flag(self):
        from ml.features import add_holiday_feature
        df = pd.DataFrame({"time": ["2026-02-27 10:00:00+00:00"],
                           "pool_uid": "SSD-5", "occupancy_pct": [50.0]})
        result = add_holiday_feature(df)
        assert result.iloc[0]["is_holiday"] in (0, 1)


class TestPoolFeatures:
    def test_adds_pool_uid_encoded(self):
        from ml.features import add_pool_features
        df = make_df()
        result = add_pool_features(df)
        assert "pool_uid_encoded" in result.columns
        assert result["pool_uid_encoded"].dtype in [int, "int64", "int32"]

    def test_same_uid_same_encoding(self):
        from ml.features import add_pool_features
        df = make_df()
        result = add_pool_features(df)
        assert result["pool_uid_encoded"].nunique() == 1

    def test_kaeferberg_is_hallenbad_type(self):
        from ml.features import add_pool_features, POOL_TYPE_ENCODING
        df = make_df(pool_uid="SSD-5")
        result = add_pool_features(df)
        assert result["pool_type"].iloc[0] == POOL_TYPE_ENCODING["hallenbad"]

    def test_kaeferberg_not_seasonal(self):
        from ml.features import add_pool_features
        df = make_df(pool_uid="SSD-5")
        result = add_pool_features(df)
        assert result["is_seasonal"].iloc[0] == 0

    def test_unknown_pool_gets_other_type(self):
        from ml.features import add_pool_features, POOL_TYPE_ENCODING
        metadata = {}  # empty metadata → unknown pool
        df = make_df(pool_uid="UNKNOWN-999")
        result = add_pool_features(df, metadata=metadata)
        assert result["pool_type"].iloc[0] == POOL_TYPE_ENCODING["other"]


class TestLagFeatures:
    def test_lag_1h_first_is_nan(self):
        from ml.features import add_lag_features
        df = make_df(n=10)
        result = add_lag_features(df)
        # First value per pool should be NaN (no previous reading)
        assert pd.isna(result["lag_1h"].iloc[0])

    def test_lag_1h_second_equals_first(self):
        from ml.features import add_lag_features
        df = make_df(n=10)
        result = add_lag_features(df)
        assert result["lag_1h"].iloc[1] == result["occupancy_pct"].iloc[0]

    def test_lag_1w_column_exists(self):
        from ml.features import add_lag_features
        df = make_df(n=48)
        result = add_lag_features(df)
        assert "lag_1w" in result.columns


class TestBuildFeatures:
    def test_build_features_returns_dataframe(self):
        from ml.features import build_features
        df = make_df(n=48)
        result = build_features(df)
        assert isinstance(result, pd.DataFrame)

    def test_build_features_has_all_columns(self):
        from ml.features import build_features, FEATURE_COLUMNS
        df = make_df(n=48)
        result = build_features(df)
        for col in FEATURE_COLUMNS:
            assert col in result.columns, f"Missing feature column: {col}"

    def test_build_features_preserves_row_count(self):
        from ml.features import build_features
        df = make_df(n=48)
        result = build_features(df)
        assert len(result) == len(df)

    def test_build_features_does_not_mutate_input(self):
        from ml.features import build_features
        df = make_df(n=48)
        original_cols = list(df.columns)
        build_features(df)
        assert list(df.columns) == original_cols
