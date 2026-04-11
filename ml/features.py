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

# Pool UIDs to exclude from the ML pipeline (not actual swimming pools)
EXCLUDED_POOLS: set[str] = {"SSD-8"}  # SSD-8 = Josel-Areal (sports hall)

# Pool type encoding (stable integer mapping)
POOL_TYPE_ENCODING = {
    "hallenbad": 0,
    "freibad": 1,
    "strandbad": 2,
    "seebad": 3,
    "other": 4,
}

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


def add_holiday_feature(
    df: pd.DataFrame, country: str = "CH", subdiv: str = "ZH"
) -> pd.DataFrame:
    """Add Swiss/Zurich public holiday flag (vectorized)."""
    df = df.copy()
    ch_holidays = _get_holidays(country=country, subdiv=subdiv)
    # Normalize to date and use `in` operator directly — this triggers the holidays library's
    # lazy year-generation, ensuring dates like 2026-01-01 are correctly resolved.
    date_only = pd.to_datetime(df["time"])
    if date_only.dt.tz is not None:
        date_only = date_only.dt.tz_convert("UTC").dt.tz_localize(None)
    df["is_holiday"] = date_only.dt.normalize().apply(
        lambda d: 1 if d.date() in ch_holidays else 0
    )
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

    uid_encoding = get_pool_uid_encoding(
        df["pool_uid"].tolist(), encoding_map=encoding_map
    )
    df["pool_uid_encoded"] = df["pool_uid"].map(uid_encoding).fillna(-1).astype(int)
    df["pool_type"] = df["pool_uid"].map(
        lambda uid: POOL_TYPE_ENCODING.get(
            metadata.get(uid, {}).get("type", "other"), 4
        )
    )
    df["is_seasonal"] = df["pool_uid"].map(
        lambda uid: int(metadata.get(uid, {}).get("seasonal", False))
    )
    return df


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add lag features per pool using time-based shifts.

    lag_1h: occupancy 1 hour ago (not 1 row — avoids mismatch when data
            is bucketed at sub-hourly intervals like 10 min).
    lag_1w: occupancy 7 days ago at the same time.
    """
    df = df.copy()
    df = df.sort_values(["pool_uid", "time"])
    df["time_dt"] = pd.to_datetime(df["time"])

    def _time_shift(group: pd.DataFrame, freq: str):
        g = group.set_index("time_dt").sort_index()
        shifted = g["occupancy_pct"].shift(freq=freq)
        return shifted.reindex(g.index).values

    lag_1h_vals: list[tuple] = []
    lag_1w_vals: list[tuple] = []
    for _, group in df.groupby("pool_uid"):
        idx = group.index
        lag_1h_vals.extend(zip(idx, _time_shift(group, "1h")))
        lag_1w_vals.extend(zip(idx, _time_shift(group, "7D")))

    df["lag_1h"] = df.index.map(dict(lag_1h_vals))
    df["lag_1w"] = df.index.map(dict(lag_1w_vals))
    df = df.drop(columns=["time_dt"])
    return df


def add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add 7-day rolling mean of occupancy per pool (time-based window)."""
    df = df.copy()
    df = df.sort_values(["pool_uid", "time"])
    df["_ts"] = pd.to_datetime(df["time"])
    df = df.set_index("_ts")
    df["rolling_mean_7d"] = df.groupby("pool_uid")["occupancy_pct"].transform(
        lambda x: x.rolling("7D", min_periods=1).mean()
    )
    df = df.reset_index(drop=True)
    return df


def add_weather_features(
    df: pd.DataFrame,
    weather_df: pd.DataFrame,
    metadata: "dict[str, dict] | None" = None,
) -> pd.DataFrame:
    """
    Merge weather data into the feature DataFrame.

    Expects df to have 'hour_of_day' and 'date' columns (from add_time_features).
    Adds: temperature_c, precipitation_mm, is_rainy, temp_x_outdoor.

    When *weather_df* contains a ``city`` column, the join uses
    ``(city, date, hour_of_day)`` so each pool receives weather for its own
    city.  The ``city`` column is derived from pool_uid via *metadata*
    (pool_metadata.json).  Pools with an unrecognised uid emit a warning and
    receive NaN weather values (filled with sensible defaults below).

    Falls back to the legacy ``(date, hour_of_day)`` join when ``city`` is
    absent from *weather_df* — preserving backward compatibility with callers
    that pass non-city-aware weather DataFrames.
    """
    df = df.copy()

    if "city" in weather_df.columns:
        # --- City-aware path ---
        if metadata is None:
            metadata = load_pool_metadata()

        city_map = {uid: meta.get("city") for uid, meta in metadata.items()}
        df["_city"] = df["pool_uid"].map(city_map)

        unknown_mask = df["_city"].isna()
        if unknown_mask.any():
            unknown_uids = df.loc[unknown_mask, "pool_uid"].unique().tolist()
            logger.warning(
                "Unknown city for pool_uid(s) %s; weather features will use defaults for those rows",
                unknown_uids,
            )

        w_cols = [
            "city",
            "date",
            "hour",
            "temperature_c",
            "precipitation_mm",
            "weathercode",
        ]
        weather_cols = weather_df[[c for c in w_cols if c in weather_df.columns]].copy()
        weather_cols = weather_cols.rename(
            columns={"hour": "hour_of_day", "city": "_city"}
        )

        df = df.merge(weather_cols, on=["_city", "date", "hour_of_day"], how="left")
        df = df.drop(columns=["_city"], errors="ignore")
    else:
        # --- Legacy path: no city column in weather_df ---
        w_cols = ["hour", "temperature_c", "precipitation_mm", "weathercode"]
        if "date" in weather_df.columns:
            w_cols = ["date"] + w_cols
        weather_cols = weather_df[w_cols].copy()
        weather_cols = weather_cols.rename(columns={"hour": "hour_of_day"})

        merge_on = (
            ["date", "hour_of_day"]
            if "date" in weather_cols.columns
            else ["hour_of_day"]
        )
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
    if EXCLUDED_POOLS:
        before = len(df)
        df = df[~df["pool_uid"].isin(EXCLUDED_POOLS)]
        dropped = before - len(df)
        if dropped:
            logger.debug("Excluded %d rows for pool_uid(s) %s", dropped, EXCLUDED_POOLS)
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
        df = add_weather_features(df, weather_df, metadata=metadata)
    else:
        # Always populate weather columns so FEATURE_COLUMNS is fully resolvable.
        # When weather is unavailable, use sensible defaults rather than relying
        # on a downstream fillna(0) that would silently use wrong values.
        df["temperature_c"] = 15.0
        df["precipitation_mm"] = 0.0
        df["is_rainy"] = 0
        is_outdoor = (df["pool_type"] == POOL_TYPE_ENCODING["freibad"]).astype(float)
        df["temp_x_outdoor"] = 15.0 * is_outdoor
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
    # Weather features — always present (defaults used when weather unavailable)
    "temperature_c",
    "precipitation_mm",
    "is_rainy",
    "temp_x_outdoor",
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
    """Add opening hours features to the DataFrame (vectorized).

    Adds columns: is_open, minutes_since_open, minutes_until_close.
    Requires hour_of_day and day_of_week columns (from add_time_features).

    Implementation: builds a schedule lookup table (31 pools × 7 days ≈ 217
    rows), merges it into df via a join, then computes all three columns with
    vectorized numpy operations. No Python-level row iteration.
    """
    df = df.copy()
    if pool_metadata is None:
        pool_metadata = load_pool_metadata()

    # --- 1. Build schedule lookup table (tiny — one row per pool×day) ---
    # open_min / close_min = -1 sentinel means "pool closed this day"
    schedule_rows: list[dict] = []
    # uid -> (open_ordinal, close_ordinal) for pools with a seasonal window
    seasonal_dict: dict[str, tuple[int, int]] = {}

    for uid, meta in pool_metadata.items():
        oh = meta.get("opening_hours")
        if oh is None:
            # No metadata → treat as always open, no seasonal restriction
            for dow_idx in range(7):
                schedule_rows.append(
                    {
                        "pool_uid": uid,
                        "day_of_week": dow_idx,
                        "open_min": 0,
                        "close_min": 1440,
                    }
                )
            continue

        # Seasonal window (e.g. Freibäder May–Sep)
        so_str = oh.get("seasonal_open")
        sc_str = oh.get("seasonal_close")
        if so_str and sc_str:
            try:
                seasonal_dict[uid] = (
                    datetime.date.fromisoformat(so_str).toordinal(),
                    datetime.date.fromisoformat(sc_str).toordinal(),
                )
            except (ValueError, TypeError):
                pass

        # Per-weekday schedule
        schedule = oh.get("schedule", {})
        for dow_idx, day_name in enumerate(_DAY_NAMES):
            day_sched = schedule.get(day_name)
            if not day_sched:
                schedule_rows.append(
                    {
                        "pool_uid": uid,
                        "day_of_week": dow_idx,
                        "open_min": -1,
                        "close_min": -1,
                    }
                )
            else:
                try:
                    oh_h, oh_m = map(int, day_sched["open"].split(":"))
                    oc_h, oc_m = map(int, day_sched["close"].split(":"))
                    schedule_rows.append(
                        {
                            "pool_uid": uid,
                            "day_of_week": dow_idx,
                            "open_min": oh_h * 60 + oh_m,
                            "close_min": oc_h * 60 + oc_m,
                        }
                    )
                except (KeyError, ValueError):
                    schedule_rows.append(
                        {
                            "pool_uid": uid,
                            "day_of_week": dow_idx,
                            "open_min": 0,
                            "close_min": 1440,
                        }
                    )

    schedule_lut = pd.DataFrame(schedule_rows)

    # --- 2. Join schedule into df (O(n), no Python loops) ---
    df = df.merge(schedule_lut, on=["pool_uid", "day_of_week"], how="left")
    df["open_min"] = df["open_min"].fillna(0).astype(int)
    df["close_min"] = df["close_min"].fillna(1440).astype(int)

    # --- 3. Seasonal check (vectorized ordinal arithmetic) ---
    # Convert timestamps to day ordinals without any Python-level iteration:
    #   days_since_unix_epoch = floor(ts_ns / ns_per_day)
    #   ordinal = days_since_unix_epoch + ordinal(1970-01-01)
    EPOCH_ORDINAL = datetime.date(1970, 1, 1).toordinal()  # 719163
    dt_series = pd.to_datetime(df["time"])
    if dt_series.dt.tz is not None:
        dt_series = dt_series.dt.tz_convert("UTC")
    row_ordinal = (
        dt_series.dt.normalize().astype("int64") // (86_400 * 10**9)
    ) + EPOCH_ORDINAL

    if seasonal_dict:
        so_map = {uid: v[0] for uid, v in seasonal_dict.items()}
        sc_map = {uid: v[1] for uid, v in seasonal_dict.items()}
        so_series = df["pool_uid"].map(so_map)
        sc_series = df["pool_uid"].map(sc_map)
        has_window = so_series.notna()
        in_season: pd.Series = ~has_window | (
            (row_ordinal >= so_series.fillna(0).astype("int64"))
            & (row_ordinal <= sc_series.fillna(10**8).astype("int64"))
        )
    else:
        in_season = pd.Series(True, index=df.index)

    # --- 4. Compute the three output columns (vectorized) ---
    current_min = df["hour_of_day"] * 60
    closed_day = df["open_min"] == -1

    is_open_mask = (
        in_season
        & ~closed_day
        & (current_min >= df["open_min"])
        & (current_min < df["close_min"])
    )

    df["is_open"] = is_open_mask.astype(int)
    df["minutes_since_open"] = np.where(is_open_mask, current_min - df["open_min"], 0)
    df["minutes_until_close"] = np.where(is_open_mask, df["close_min"] - current_min, 0)

    df = df.drop(columns=["open_min", "close_min"], errors="ignore")
    return df
