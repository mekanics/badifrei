"""Unit tests for feature engineering module."""

import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest


def make_df(n=48, pool_uid="SSD-5", start="2026-02-01"):
    """Create a minimal test DataFrame."""
    times = pd.date_range(start=start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "time": times,
            "pool_uid": pool_uid,
            "occupancy_pct": [float(i % 100) for i in range(n)],
        }
    )


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
        df = pd.DataFrame(
            {
                "time": ["2026-02-07 10:00:00+00:00", "2026-02-09 10:00:00+00:00"],
                "pool_uid": "SSD-5",
                "occupancy_pct": [50.0, 30.0],
            }
        )
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

        df = pd.DataFrame(
            {
                "time": ["2026-01-01 10:00:00+00:00"],
                "pool_uid": "SSD-5",
                "occupancy_pct": [50.0],
            }
        )
        result = add_holiday_feature(df)
        assert result.iloc[0]["is_holiday"] == 1

    def test_regular_day_not_holiday(self):
        from ml.features import add_holiday_feature

        df = pd.DataFrame(
            {
                "time": ["2026-02-27 10:00:00+00:00"],
                "pool_uid": "SSD-5",
                "occupancy_pct": [50.0],
            }
        )
        result = add_holiday_feature(df)
        assert result.iloc[0]["is_holiday"] == 0

    def test_returns_integer_flag(self):
        from ml.features import add_holiday_feature

        df = pd.DataFrame(
            {
                "time": ["2026-02-27 10:00:00+00:00"],
                "pool_uid": "SSD-5",
                "occupancy_pct": [50.0],
            }
        )
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
        assert pd.isna(result["lag_1h"].iloc[0])

    def test_lag_1h_second_equals_first_with_hourly_data(self):
        from ml.features import add_lag_features

        df = make_df(n=10)  # freq="1h" — shift(freq="1h") matches row shift
        result = add_lag_features(df)
        assert result["lag_1h"].iloc[1] == result["occupancy_pct"].iloc[0]

    def test_lag_1h_nan_for_subhourly_buckets(self):
        """With 10-min buckets, lag_1h should be NaN for the first 5 rows
        (rows 0-5 are within the first hour)."""
        from ml.features import add_lag_features

        times = pd.date_range(start="2026-02-01", periods=12, freq="10min", tz="UTC")
        df = pd.DataFrame(
            {
                "time": times,
                "pool_uid": "SSD-5",
                "occupancy_pct": [float(i * 10) for i in range(12)],
            }
        )
        result = add_lag_features(df)
        # Row at T+10min has no reading from T+10min - 1h, so lag_1h is NaN
        assert pd.isna(result["lag_1h"].iloc[1])
        # Row at T+60min should get the value from T+0min
        assert result["lag_1h"].iloc[6] == result["occupancy_pct"].iloc[0]

    def test_lag_1w_column_exists(self):
        from ml.features import add_lag_features

        df = make_df(n=48)
        result = add_lag_features(df)
        assert "lag_1w" in result.columns


class TestRollingFeatures:
    def test_rolling_mean_7d_column_exists(self):
        from ml.features import add_rolling_features

        df = make_df(n=48)
        result = add_rolling_features(df)
        assert "rolling_mean_7d" in result.columns

    def test_rolling_mean_uses_time_window(self):
        """With 10-min buckets, rolling('7D') should cover 7 days of data,
        not just 168 rows (which would be ~28h at 10-min intervals)."""
        from ml.features import add_rolling_features
        import numpy as np

        n = 7 * 24 * 6 + 1  # 7 days of 10-min data + 1 extra
        times = pd.date_range(start="2026-01-01", periods=n, freq="10min", tz="UTC")
        df = pd.DataFrame(
            {
                "time": times,
                "pool_uid": "SSD-5",
                "occupancy_pct": np.ones(n) * 50.0,
            }
        )
        # Set first day to 100, rest to 0 — rolling 7D should include the first day
        df.loc[df.index[:144], "occupancy_pct"] = (
            100.0  # first 24h at 10-min = 144 rows
        )
        df.loc[df.index[144:], "occupancy_pct"] = 0.0
        result = add_rolling_features(df)
        # The last row (day 7 + 10min) should have a rolling mean that includes
        # day 1 data (100) — if window were only 168 rows it would miss it
        last_mean = result["rolling_mean_7d"].iloc[-1]
        assert last_mean > 0, "Rolling mean should include data from 7 days ago"


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


class TestExcludedPools:
    def test_excluded_pool_rows_are_dropped(self):
        from ml.features import build_features, EXCLUDED_POOLS

        # Create df with both a normal pool and an excluded one
        normal = make_df(n=48, pool_uid="SSD-5")
        excluded = make_df(n=48, pool_uid="SSD-8")
        df = pd.concat([normal, excluded], ignore_index=True)
        result = build_features(df)
        assert "SSD-8" not in result["pool_uid"].values
        assert "SSD-5" in result["pool_uid"].values

    def test_excluded_pool_reduces_row_count(self):
        from ml.features import build_features

        normal = make_df(n=48, pool_uid="SSD-5")
        excluded = make_df(n=48, pool_uid="SSD-8")
        df = pd.concat([normal, excluded], ignore_index=True)
        result = build_features(df)
        assert len(result) == 48  # only SSD-5 rows remain

    def test_all_excluded_returns_empty(self):
        from ml.features import build_features

        df = make_df(n=48, pool_uid="SSD-8")
        result = build_features(df)
        assert len(result) == 0

    def test_ssd8_in_excluded_pools_constant(self):
        from ml.features import EXCLUDED_POOLS

        assert "SSD-8" in EXCLUDED_POOLS


def make_weather_df(
    temperature_c: float = 22.0, precipitation_mm: float = 0.0, weathercode: int = 0
) -> pd.DataFrame:
    """Create a minimal 24-row weather DataFrame."""
    return pd.DataFrame(
        {
            "hour": list(range(24)),
            "temperature_c": [temperature_c] * 24,
            "precipitation_mm": [precipitation_mm] * 24,
            "weathercode": [weathercode] * 24,
        }
    )


class TestWeatherFeatures:
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
        weather = pd.DataFrame(
            {
                "hour": list(range(12)),
                "temperature_c": [np.nan] * 12,
                "precipitation_mm": [np.nan] * 12,
                "weathercode": [np.nan] * 12,
            }
        )
        result = build_features(df, weather_df=weather)
        assert not result["temperature_c"].isna().any(), "NaN temperature not filled"
        assert (
            not result["precipitation_mm"].isna().any()
        ), "NaN precipitation not filled"
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


# Mock opening hours metadata for tests
_MOCK_OPENING_HOURS = {
    "schedule": {
        "Mon": {"open": "06:00", "close": "22:00"},
        "Tue": {"open": "06:00", "close": "22:00"},
        "Wed": {"open": "06:00", "close": "22:00"},
        "Thu": {"open": "06:00", "close": "22:00"},
        "Fri": {"open": "06:00", "close": "22:00"},
        "Sat": {"open": "08:00", "close": "20:00"},
        "Sun": None,
    },
    "seasonal_close": None,
    "seasonal_open": None,
}

_MOCK_METADATA = {
    "TEST-1": {
        "uid": "TEST-1",
        "name": "Test Pool",
        "type": "hallenbad",
        "seasonal": False,
        "opening_hours": _MOCK_OPENING_HOURS,
    }
}


class TestOpeningHoursFeatures:
    """Tests for add_opening_hours_features()."""

    def _make_single_row(
        self, hour: int, day_of_week: int, pool_uid: str = "TEST-1"
    ) -> pd.DataFrame:
        """Make a single-row DataFrame with hour_of_day and day_of_week set."""
        # 2026-02-02 is a Monday (day_of_week=0).
        # Use Europe/Zurich so hour_of_day matches the hour parameter after
        # add_time_features converts to local time.
        from datetime import timedelta

        base = pd.Timestamp("2026-02-02 00:00:00", tz="Europe/Zurich")
        t = base + pd.Timedelta(days=day_of_week, hours=hour)
        df = pd.DataFrame({"time": [t], "pool_uid": pool_uid, "occupancy_pct": [50.0]})
        from ml.features import add_time_features

        return add_time_features(df)

    def test_midnight_is_closed(self):
        from ml.features import add_opening_hours_features

        df = self._make_single_row(hour=0, day_of_week=0)  # Mon midnight
        result = add_opening_hours_features(df, _MOCK_METADATA)
        assert result.iloc[0]["is_open"] == 0

    def test_morning_hour_is_open(self):
        from ml.features import add_opening_hours_features

        # Hour 7 on Monday → open (06:00–22:00)
        # minutes_since_open = (7-6)*60 = 60
        # minutes_until_close = (22-7)*60 = 900
        df = self._make_single_row(hour=7, day_of_week=0)
        result = add_opening_hours_features(df, _MOCK_METADATA)
        row = result.iloc[0]
        assert row["is_open"] == 1
        assert row["minutes_since_open"] == 60
        assert row["minutes_until_close"] == 900

    def test_hour_22_is_closed(self):
        from ml.features import add_opening_hours_features

        # Hour 22 → closed (close is 22:00, condition is hour < 22:00)
        df = self._make_single_row(hour=22, day_of_week=0)
        result = add_opening_hours_features(df, _MOCK_METADATA)
        assert result.iloc[0]["is_open"] == 0

    def test_sunday_is_closed(self):
        from ml.features import add_opening_hours_features

        df = self._make_single_row(hour=12, day_of_week=6)  # Sunday noon
        result = add_opening_hours_features(df, _MOCK_METADATA)
        assert result.iloc[0]["is_open"] == 0

    def test_defensive_default_unknown_pool(self):
        from ml.features import add_opening_hours_features

        df = self._make_single_row(hour=0, day_of_week=0, pool_uid="UNKNOWN-999")
        result = add_opening_hours_features(df, _MOCK_METADATA)
        row = result.iloc[0]
        # Pool not in metadata → treat as always open with full-day defaults
        assert row["is_open"] == 1
        assert row["minutes_since_open"] == 0
        assert row["minutes_until_close"] == 1440

    def test_defensive_default_no_opening_hours_key(self):
        from ml.features import add_opening_hours_features

        # Pool exists in metadata but has no opening_hours key
        metadata = {
            "TEST-2": {"uid": "TEST-2", "name": "No Hours Pool", "type": "hallenbad"}
        }
        df = self._make_single_row(hour=3, day_of_week=0, pool_uid="TEST-2")
        result = add_opening_hours_features(df, metadata)
        assert result.iloc[0]["is_open"] == 1

    def test_build_features_includes_opening_hours(self):
        from ml.features import build_features, OPENING_HOURS_FEATURE_COLUMNS

        # build_features should add opening hours columns (uses real metadata — no hours data yet → defaults)
        df = make_df(n=24)
        result = build_features(df)
        for col in OPENING_HOURS_FEATURE_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"


class TestTimezoneConversion:
    """Verify that UTC timestamps are converted to Europe/Zurich for features."""

    def test_utc_to_zurich_winter(self):
        """In CET (winter), UTC+1: 05:00 UTC → 06:00 Zurich."""
        from ml.features import add_time_features

        df = pd.DataFrame(
            {
                "time": [pd.Timestamp("2026-02-02 05:00:00", tz="UTC")],
                "pool_uid": "SSD-5",
                "occupancy_pct": [50.0],
            }
        )
        result = add_time_features(df)
        assert result.iloc[0]["hour_of_day"] == 6

    def test_utc_to_zurich_summer(self):
        """In CEST (summer), UTC+2: 04:00 UTC → 06:00 Zurich."""
        from ml.features import add_time_features

        df = pd.DataFrame(
            {
                "time": [pd.Timestamp("2026-07-15 04:00:00", tz="UTC")],
                "pool_uid": "SSD-5",
                "occupancy_pct": [50.0],
            }
        )
        result = add_time_features(df)
        assert result.iloc[0]["hour_of_day"] == 6

    def test_utc_midnight_crosses_to_next_zurich_day(self):
        """22:00 UTC in summer = 00:00 next day in Zurich."""
        from ml.features import add_time_features

        df = pd.DataFrame(
            {
                "time": [pd.Timestamp("2026-07-15 22:00:00", tz="UTC")],
                "pool_uid": "SSD-5",
                "occupancy_pct": [50.0],
            }
        )
        result = add_time_features(df)
        assert result.iloc[0]["hour_of_day"] == 0
        assert result.iloc[0]["day_of_week"] == 3  # Wednesday→Thursday

    def test_naive_timestamps_unchanged(self):
        """Naive timestamps (no tz) keep their raw hour — no conversion."""
        from ml.features import add_time_features

        df = pd.DataFrame(
            {
                "time": ["2026-02-02 07:00:00"],
                "pool_uid": "SSD-5",
                "occupancy_pct": [50.0],
            }
        )
        result = add_time_features(df)
        assert result.iloc[0]["hour_of_day"] == 7

    def test_is_open_aligns_with_zurich_hours(self):
        """Pool opening at 06:00 Zurich should be open at 04:00 UTC in summer."""
        from ml.features import add_time_features, add_opening_hours_features

        metadata = {
            "TEST-1": {
                "uid": "TEST-1",
                "name": "Test Pool",
                "type": "hallenbad",
                "opening_hours": {
                    "schedule": {
                        "Mon": {"open": "06:00", "close": "22:00"},
                        "Tue": {"open": "06:00", "close": "22:00"},
                        "Wed": {"open": "06:00", "close": "22:00"},
                        "Thu": {"open": "06:00", "close": "22:00"},
                        "Fri": {"open": "06:00", "close": "22:00"},
                        "Sat": {"open": "06:00", "close": "22:00"},
                        "Sun": None,
                    }
                },
            }
        }
        # 04:00 UTC on Wednesday 2026-07-15 = 06:00 CEST → pool just opened
        df = pd.DataFrame(
            {
                "time": [pd.Timestamp("2026-07-15 04:00:00", tz="UTC")],
                "pool_uid": "TEST-1",
                "occupancy_pct": [30.0],
            }
        )
        df = add_time_features(df)
        result = add_opening_hours_features(df, metadata)
        assert result.iloc[0]["is_open"] == 1
        assert result.iloc[0]["minutes_since_open"] == 0

    def test_is_open_closing_hour_zurich(self):
        """Pool closing at 22:00 Zurich should be closed at 20:00 UTC in summer."""
        from ml.features import add_time_features, add_opening_hours_features

        metadata = {
            "TEST-1": {
                "uid": "TEST-1",
                "name": "Test Pool",
                "type": "hallenbad",
                "opening_hours": {
                    "schedule": {
                        "Mon": {"open": "06:00", "close": "22:00"},
                        "Tue": {"open": "06:00", "close": "22:00"},
                        "Wed": {"open": "06:00", "close": "22:00"},
                        "Thu": {"open": "06:00", "close": "22:00"},
                        "Fri": {"open": "06:00", "close": "22:00"},
                        "Sat": {"open": "06:00", "close": "22:00"},
                        "Sun": None,
                    }
                },
            }
        }
        # 20:00 UTC on Wednesday 2026-07-15 = 22:00 CEST → pool closing
        df = pd.DataFrame(
            {
                "time": [pd.Timestamp("2026-07-15 20:00:00", tz="UTC")],
                "pool_uid": "TEST-1",
                "occupancy_pct": [5.0],
            }
        )
        df = add_time_features(df)
        result = add_opening_hours_features(df, metadata)
        assert result.iloc[0]["is_open"] == 0
