"""Feature engineering for pool occupancy prediction."""
import datetime
import json
import logging
from pathlib import Path

import holidays
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

METADATA_PATH = Path(__file__).parent / "pool_metadata.json"

# Pool type encoding (stable integer mapping)
POOL_TYPE_ENCODING = {"hallenbad": 0, "freibad": 1, "strandbad": 2, "seebad": 3, "other": 4}

# Module-level holiday cache — avoids reconstructing on every call
_HOLIDAYS_CACHE: dict[str, "holidays.HolidayBase"] = {}


def _get_holidays(country: str = "CH", subdiv: str = "ZH") -> "holidays.HolidayBase":
    key = f"{country}_{subdiv}"
    if key not in _HOLIDAYS_CACHE:
        _HOLIDAYS_CACHE[key] = holidays.country_holidays(country, subdiv=subdiv)
    return _HOLIDAYS_CACHE[key]


def load_pool_metadata() -> dict[str, dict]:
    """Load pool metadata keyed by uid."""
    with open(METADATA_PATH) as f:
        data = json.load(f)
    return {p["uid"]: p for p in data}


def get_pool_uid_encoding(
    uids: list[str],
    encoding_map: dict[str, int] | None = None,
) -> dict[str, int]:
    """Return stable integer encoding for pool UIDs.

    If *encoding_map* is provided (e.g. loaded from the model sidecar JSON),
    it is returned as-is.  Otherwise a new mapping is derived from *uids*
    by sorting them for determinism — this is only safe at training time when
    the full population of UIDs is present in the DataFrame.
    """
    if encoding_map is not None:
        return encoding_map
    return {uid: i for i, uid in enumerate(sorted(set(uids)))}


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add time-based features from the 'time' column."""
    df = df.copy()
    dt = pd.to_datetime(df["time"])
    df["date"] = dt.dt.date  # calendar date (for weather merge)
    df["hour_of_day"] = dt.dt.hour
    df["day_of_week"] = dt.dt.dayofweek  # 0=Monday, 6=Sunday
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month"] = dt.dt.month
    df["day_of_year"] = dt.dt.dayofyear
    return df


def add_holiday_feature(df: pd.DataFrame, country: str = "CH", subdiv: str = "ZH") -> pd.DataFrame:
    """Add Swiss/Zurich public holiday flag."""
    df = df.copy()
    ch_holidays = _get_holidays(country=country, subdiv=subdiv)
    dt = pd.to_datetime(df["time"])
    df["is_holiday"] = dt.dt.date.apply(lambda d: int(d in ch_holidays))
    return df


def add_pool_features(
    df: pd.DataFrame,
    metadata: dict[str, dict] | None = None,
    encoding_map: dict[str, int] | None = None,
) -> pd.DataFrame:
    """Add pool type encoding and metadata features."""
    df = df.copy()
    if metadata is None:
        metadata = load_pool_metadata()

    uid_encoding = get_pool_uid_encoding(df["pool_uid"].tolist(), encoding_map=encoding_map)
    df["pool_uid_encoded"] = df["pool_uid"].map(uid_encoding).fillna(-1).astype(int)
    df["pool_type"] = df["pool_uid"].map(
        lambda uid: POOL_TYPE_ENCODING.get(metadata.get(uid, {}).get("type", "other"), 4)
    )
    df["is_seasonal"] = df["pool_uid"].map(
        lambda uid: int(metadata.get(uid, {}).get("seasonal", False))
    )
    return df


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add lag features per pool. Requires sorted data."""
    df = df.copy()
    df = df.sort_values(["pool_uid", "time"])

    # Lag 1h: previous reading for same pool
    df["lag_1h"] = df.groupby("pool_uid")["occupancy_pct"].shift(1)

    # Lag 1w: same time last week (approx 7*24=168 rows if hourly — use time-based approach)
    df["time_dt"] = pd.to_datetime(df["time"])

    def lag_1w_for_group(group):
        group = group.set_index("time_dt").sort_index()
        shifted = group["occupancy_pct"].shift(freq="7D")
        return shifted.reindex(group.index).values

    lag_1w_values = []
    for uid, group in df.groupby("pool_uid"):
        vals = lag_1w_for_group(group)
        lag_1w_values.extend(zip(group.index, vals))

    lag_1w_map = dict(lag_1w_values)
    df["lag_1w"] = df.index.map(lag_1w_map)
    df = df.drop(columns=["time_dt"])
    return df


def add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add 7-day rolling mean of occupancy per pool."""
    df = df.copy()
    df = df.sort_values(["pool_uid", "time"])
    df["rolling_mean_7d"] = df.groupby("pool_uid")["occupancy_pct"].transform(
        lambda x: x.rolling(window=7 * 24, min_periods=1).mean()
    )
    return df


def add_weather_features(df: pd.DataFrame, weather_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge weather data into the feature DataFrame.

    Expects df to have an 'hour_of_day' column (from add_time_features).
    Adds: temperature_c, precipitation_mm, is_rainy, temp_x_outdoor.
    """
    df = df.copy()
    # weather_df may or may not have a 'date' column — support both
    w_cols = ["hour", "temperature_c", "precipitation_mm", "weathercode"]
    if "date" in weather_df.columns:
        w_cols = ["date"] + w_cols
    weather_cols = weather_df[w_cols].copy()
    weather_cols = weather_cols.rename(columns={"hour": "hour_of_day"})

    # Merge on date+hour if date is available, otherwise hour only
    merge_on = ["date", "hour_of_day"] if "date" in weather_cols.columns else ["hour_of_day"]
    df = df.merge(weather_cols, on=merge_on, how="left")

    # Fill NaN weather with sensible defaults
    df["temperature_c"] = df["temperature_c"].fillna(15.0)
    df["precipitation_mm"] = df["precipitation_mm"].fillna(0.0)
    df["weathercode"] = df["weathercode"].fillna(0.0)

    # is_rainy: weathercode >= 51 (drizzle / rain threshold in WMO codes)
    df["is_rainy"] = (df["weathercode"] >= 51).astype(int)

    # temp_x_outdoor: temperature * outdoor flag (pool_type encoded 1 = freibad)
    is_outdoor = (df["pool_type"] == POOL_TYPE_ENCODING["freibad"]).astype(float)
    df["temp_x_outdoor"] = df["temperature_c"] * is_outdoor

    df = df.drop(columns=["weathercode"])
    return df


def build_features(
    df: pd.DataFrame,
    metadata: dict[str, dict] | None = None,
    weather_df: "pd.DataFrame | None" = None,
    encoding_map: dict[str, int] | None = None,
    lag_1h_override: float | None = None,
    lag_1w_override: float | None = None,
) -> pd.DataFrame:
    """
    Full feature pipeline. Input df must have columns:
    - time (datetime or string)
    - pool_uid (string)
    - occupancy_pct (float)

    Optional:
    - weather_df: hourly weather DataFrame (columns: hour, temperature_c,
      precipitation_mm, weathercode). When provided, weather features are
      merged in and four new columns are added: temperature_c,
      precipitation_mm, is_rainy, temp_x_outdoor.
    - encoding_map: pre-computed uid→int mapping (required at inference to
      match training-time encoding; if None it is derived from the DataFrame).
    - lag_1h_override: when provided, overrides the lag_1h column (use at
      inference to supply the most-recent real occupancy reading).
    - lag_1w_override: when provided, overrides the lag_1w column (use at
      inference to supply the reading from 7 days ago).

    Returns df with all features added.
    """
    df = df.copy()
    df = add_time_features(df)
    df = add_holiday_feature(df)
    df = add_pool_features(df, metadata, encoding_map=encoding_map)
    df = add_lag_features(df)
    df = add_rolling_features(df)
    if lag_1h_override is not None:
        df["lag_1h"] = lag_1h_override
    if lag_1w_override is not None:
        df["lag_1w"] = lag_1w_override
    if weather_df is not None:
        df = add_weather_features(df, weather_df)
    df = add_opening_hours_features(df, metadata)
    # Drop helper columns not used as model features
    df = df.drop(columns=["date"], errors="ignore")
    return df


WEATHER_FEATURE_COLUMNS = [
    "temperature_c",
    "precipitation_mm",
    "is_rainy",
    "temp_x_outdoor",
]

OPENING_HOURS_FEATURE_COLUMNS = [
    "is_open",
    "minutes_since_open",
    "minutes_until_close",
]

FEATURE_COLUMNS = [
    "hour_of_day",
    "day_of_week",
    "is_weekend",
    "month",
    "day_of_year",
    "is_holiday",
    "pool_uid_encoded",
    "pool_type",
    "is_seasonal",
    "lag_1h",
    "lag_1w",
    "rolling_mean_7d",
    "is_open",
    "minutes_since_open",
    "minutes_until_close",
]

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def compute_opening_hours_for_row(
    hour: int,
    day_of_week: int,
    opening_hours: dict | None,
    date: "datetime.date | None" = None,
) -> tuple[int, int, int]:
    """Compute is_open, minutes_since_open, minutes_until_close for a single row.

    Args:
        hour: hour of day (0-23)
        day_of_week: 0=Mon ... 6=Sun
        opening_hours: the pool's opening_hours dict (may be None)
        date: calendar date for the row (used to check seasonal windows)

    Returns:
        (is_open, minutes_since_open, minutes_until_close)
    """
    if opening_hours is None:
        return 1, 0, 0  # defensive: treat as always open

    # Check seasonal window (Freibäder etc. are only open part of the year)
    if date is not None:
        seasonal_open = opening_hours.get("seasonal_open")
        seasonal_close = opening_hours.get("seasonal_close")
        if seasonal_open and seasonal_close:
            try:
                so = datetime.date.fromisoformat(seasonal_open)
                sc = datetime.date.fromisoformat(seasonal_close)
                if not (so <= date <= sc):
                    return 0, 0, 0  # outside seasonal window — pool closed
            except (ValueError, TypeError):
                pass  # malformed date — don't block

    schedule = opening_hours.get("schedule", {})
    day_name = _DAY_NAMES[day_of_week]
    day_schedule = schedule.get(day_name)

    if not day_schedule:
        # Pool closed on this day
        return 0, 0, 0

    try:
        open_h, open_m = map(int, day_schedule["open"].split(":"))
        close_h, close_m = map(int, day_schedule["close"].split(":"))
    except (KeyError, ValueError):
        return 1, 0, 0  # defensive

    open_minutes = open_h * 60 + open_m
    close_minutes = close_h * 60 + close_m
    current_minutes = hour * 60  # beginning of the hour

    if open_minutes <= current_minutes < close_minutes:
        is_open = 1
        minutes_since_open = current_minutes - open_minutes
        minutes_until_close = close_minutes - current_minutes
    else:
        is_open = 0
        minutes_since_open = 0
        minutes_until_close = 0

    return is_open, minutes_since_open, minutes_until_close


def add_opening_hours_features(
    df: pd.DataFrame,
    pool_metadata: dict[str, dict] | None = None,
) -> pd.DataFrame:
    """Add opening hours features to the DataFrame.

    Adds columns: is_open, minutes_since_open, minutes_until_close.
    Requires hour_of_day and day_of_week columns (from add_time_features).
    Defaults to is_open=1, minutes_since_open=0, minutes_until_close=0 when
    no opening hours data is available for a pool (defensive).
    """
    df = df.copy()
    if pool_metadata is None:
        pool_metadata = load_pool_metadata()

    rows_is_open = []
    rows_since_open = []
    rows_until_close = []

    for _, row in df.iterrows():
        uid = row.get("pool_uid")
        hour = int(row.get("hour_of_day", 0))
        dow = int(row.get("day_of_week", 0))
        meta = pool_metadata.get(uid, {}) if pool_metadata else {}
        opening_hours = meta.get("opening_hours", None)
        time_val = row.get("time")
        row_date = None
        if time_val is not None:
            try:
                row_date = pd.Timestamp(time_val).date()
            except Exception:
                pass
        is_open, since_open, until_close = compute_opening_hours_for_row(
            hour, dow, opening_hours, date=row_date
        )
        rows_is_open.append(is_open)
        rows_since_open.append(since_open)
        rows_until_close.append(until_close)

    df["is_open"] = rows_is_open
    df["minutes_since_open"] = rows_since_open
    df["minutes_until_close"] = rows_until_close
    return df
