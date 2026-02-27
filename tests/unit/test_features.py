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


def make_weather_df(temperature_c: float = 22.0, precipitation_mm: float = 0.0, weathercode: int = 0) -> pd.DataFrame:
    """Create a minimal 24-row weather DataFrame."""
    return pd.DataFrame({
        "hour": list(range(24)),
        "temperature_c": [temperature_c] * 24,
        "precipitation_mm": [precipitation_mm] * 24,
        "weathercode": [weathercode] * 24,
    })


class TestWeatherFeatures:
    def test_no_weather_df_does_not_add_weather_columns(self):
        from ml.features import build_features
        df = make_df(n=48)
        result = build_features(df)
        assert "temperature_c" not in result.columns
        assert "is_rainy" not in result.columns

    def test_weather_df_adds_temperature_column(self):
        from ml.features import build_features
        df = make_df(n=48)
        weather = make_weather_df(temperature_c=25.0)
        result = build_features(df, weather_df=weather)
        assert "temperature_c" in result.columns
        assert (result["temperature_c"] == 25.0).all()

    def test_weather_df_adds_precipitation_column(self):
        from ml.features import build_features
        df = make_df(n=48)
        weather = make_weather_df(precipitation_mm=3.5)
        result = build_features(df, weather_df=weather)
        assert "precipitation_mm" in result.columns
        assert (result["precipitation_mm"] == 3.5).all()

    def test_is_rainy_true_when_weathercode_ge_51(self):
        from ml.features import build_features
        df = make_df(n=48)
        weather = make_weather_df(weathercode=61)  # moderate rain
        result = build_features(df, weather_df=weather)
        assert "is_rainy" in result.columns
        assert (result["is_rainy"] == 1).all()

    def test_is_rainy_false_when_weathercode_lt_51(self):
        from ml.features import build_features
        df = make_df(n=48)
        weather = make_weather_df(weathercode=2)  # clear sky
        result = build_features(df, weather_df=weather)
        assert (result["is_rainy"] == 0).all()

    def test_nan_weather_filled_with_defaults(self):
        import numpy as np
        from ml.features import build_features
        df = make_df(n=48)
        # Only provide 12 hours of weather — the rest should be NaN-filled
        weather = pd.DataFrame({
            "hour": list(range(12)),
            "temperature_c": [np.nan] * 12,
            "precipitation_mm": [np.nan] * 12,
            "weathercode": [np.nan] * 12,
        })
        result = build_features(df, weather_df=weather)
        assert not result["temperature_c"].isna().any(), "NaN temperature not filled"
        assert not result["precipitation_mm"].isna().any(), "NaN precipitation not filled"
        assert (result.loc[result["hour_of_day"] < 12, "temperature_c"] == 15.0).all()

    def test_temp_x_outdoor_zero_for_hallenbad(self):
        from ml.features import build_features
        # SSD-5 is a hallenbad → outdoor flag = 0 → temp_x_outdoor = 0
        df = make_df(n=48, pool_uid="SSD-5")
        weather = make_weather_df(temperature_c=30.0)
        result = build_features(df, weather_df=weather)
        assert "temp_x_outdoor" in result.columns
        assert (result["temp_x_outdoor"] == 0.0).all()

    def test_temp_x_outdoor_nonzero_for_freibad(self):
        from ml.features import build_features, POOL_TYPE_ENCODING
        # Use a freibad pool via custom metadata
        metadata = {"FREI-1": {"type": "freibad", "seasonal": True}}
        df = make_df(n=48, pool_uid="FREI-1")
        weather = make_weather_df(temperature_c=30.0)
        result = build_features(df, metadata=metadata, weather_df=weather)
        assert (result["temp_x_outdoor"] == 30.0).all()

    def test_weather_features_backward_compat_default_none(self):
        from ml.features import build_features, FEATURE_COLUMNS
        """Calling build_features without weather_df still produces all base columns."""
        df = make_df(n=48)
        result = build_features(df)
        for col in FEATURE_COLUMNS:
            assert col in result.columns
