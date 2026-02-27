"""Feature engineering for pool occupancy prediction."""
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


def load_pool_metadata() -> dict[str, dict]:
    """Load pool metadata keyed by uid."""
    with open(METADATA_PATH) as f:
        data = json.load(f)
    return {p["uid"]: p for p in data}


def get_pool_uid_encoding(uids: list[str]) -> dict[str, int]:
    """Create stable integer encoding for pool UIDs (sorted for determinism)."""
    return {uid: i for i, uid in enumerate(sorted(set(uids)))}


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add time-based features from the 'time' column."""
    df = df.copy()
    dt = pd.to_datetime(df["time"])
    df["hour_of_day"] = dt.dt.hour
    df["day_of_week"] = dt.dt.dayofweek  # 0=Monday, 6=Sunday
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month"] = dt.dt.month
    df["day_of_year"] = dt.dt.dayofyear
    return df


def add_holiday_feature(df: pd.DataFrame, country: str = "CH", subdiv: str = "ZH") -> pd.DataFrame:
    """Add Swiss/Zurich public holiday flag."""
    df = df.copy()
    ch_holidays = holidays.Switzerland(subdiv=subdiv)
    dt = pd.to_datetime(df["time"])
    df["is_holiday"] = dt.dt.date.apply(lambda d: int(d in ch_holidays))
    return df


def add_pool_features(df: pd.DataFrame, metadata: dict[str, dict] | None = None) -> pd.DataFrame:
    """Add pool type encoding and metadata features."""
    df = df.copy()
    if metadata is None:
        metadata = load_pool_metadata()

    uid_encoding = get_pool_uid_encoding(df["pool_uid"].tolist())
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


def build_features(df: pd.DataFrame, metadata: dict[str, dict] | None = None) -> pd.DataFrame:
    """
    Full feature pipeline. Input df must have columns:
    - time (datetime or string)
    - pool_uid (string)
    - occupancy_pct (float)

    Returns df with all features added.
    """
    df = df.copy()
    df = add_time_features(df)
    df = add_holiday_feature(df)
    df = add_pool_features(df, metadata)
    df = add_lag_features(df)
    df = add_rolling_features(df)
    return df


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
]
