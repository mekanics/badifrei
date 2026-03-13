"""Load pool occupancy data from TimescaleDB into pandas DataFrames."""
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import pandas as pd

from collector.config import settings

logger = logging.getLogger(__name__)


class InsufficientDataError(Exception):
    """Raised when not enough data is available for training."""
    pass


MIN_RECORDS = 1000  # Require at least 1000 records for training

# Default time-bucket interval for downsampling.
# Callers (retrain.py, scripts/train.py) read TRAINING_BUCKET_INTERVAL from
# the environment and fall back to this constant — it is defined here in ONE
# place so there is no duplication.
DEFAULT_BUCKET_INTERVAL = "10 minutes"


async def load_data(
    start: Optional[datetime],
    end: datetime,
    db_url: Optional[str] = None,
    min_records: int = MIN_RECORDS,
    bucket_interval: Optional[str] = DEFAULT_BUCKET_INTERVAL,
) -> pd.DataFrame:
    """
    Load pool occupancy data from TimescaleDB.

    Returns DataFrame with columns:
    - time (datetime, UTC)
    - pool_uid (str)
    - pool_name (str)
    - current_fill (int)
    - max_space (int)
    - free_space (int)
    - occupancy_pct (float)

    Raises InsufficientDataError if fewer than min_records rows returned.
    Excludes pools with max_space = 0.

    When start is None, all available history is used (no lower bound).

    When bucket_interval is a non-empty string (e.g. "10 minutes"), the query
    uses TimescaleDB's time_bucket() with AVG() aggregates to downsample the
    data, dramatically reducing memory usage during training.  The bucket
    column is aliased to "time" so the rest of the pipeline is unchanged.
    The interval is passed as a parameterised query value (NOT f-string
    interpolated) to prevent SQL injection.

    When bucket_interval is None or "" the existing raw SELECT * query is used
    (backward-compatible).
    """
    url = db_url or settings.database_url

    conn = await asyncpg.connect(url)
    try:
        use_bucket = bool(bucket_interval)

        if use_bucket:
            if start is None:
                rows = await conn.fetch(
                    """
                    SELECT
                        time_bucket($1::interval, time) AS time,
                        pool_uid,
                        pool_name,
                        AVG(current_fill) AS current_fill,
                        max_space,
                        AVG(free_space) AS free_space,
                        AVG(occupancy_pct) AS occupancy_pct
                    FROM pool_occupancy
                    WHERE time <= $2
                      AND max_space > 0
                    GROUP BY time_bucket($1::interval, time), pool_uid, pool_name, max_space
                    ORDER BY pool_uid, time ASC
                    """,
                    bucket_interval,
                    end,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT
                        time_bucket($1::interval, time) AS time,
                        pool_uid,
                        pool_name,
                        AVG(current_fill) AS current_fill,
                        max_space,
                        AVG(free_space) AS free_space,
                        AVG(occupancy_pct) AS occupancy_pct
                    FROM pool_occupancy
                    WHERE time >= $2
                      AND time <= $3
                      AND max_space > 0
                    GROUP BY time_bucket($1::interval, time), pool_uid, pool_name, max_space
                    ORDER BY pool_uid, time ASC
                    """,
                    bucket_interval,
                    start,
                    end,
                )
        else:
            if start is None:
                rows = await conn.fetch(
                    """
                    SELECT
                        time,
                        pool_uid,
                        pool_name,
                        current_fill,
                        max_space,
                        free_space,
                        occupancy_pct
                    FROM pool_occupancy
                    WHERE time <= $1
                      AND max_space > 0
                    ORDER BY pool_uid, time ASC
                    """,
                    end,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT
                        time,
                        pool_uid,
                        pool_name,
                        current_fill,
                        max_space,
                        free_space,
                        occupancy_pct
                    FROM pool_occupancy
                    WHERE time >= $1
                      AND time <= $2
                      AND max_space > 0
                    ORDER BY pool_uid, time ASC
                    """,
                    start,
                    end,
                )
    finally:
        await conn.close()

    if len(rows) < min_records:
        raise InsufficientDataError(
            f"Only {len(rows)} records found (minimum {min_records} required). "
            f"Keep collecting data and try again later."
        )

    df = pd.DataFrame(rows, columns=[
        "time", "pool_uid", "pool_name",
        "current_fill", "max_space", "free_space", "occupancy_pct"
    ])

    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["occupancy_pct"] = df["occupancy_pct"].astype(float)

    logger.info(f"Loaded {len(df)} records for {df['pool_uid'].nunique()} pools")
    return df
