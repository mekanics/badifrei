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


async def load_data(
    start: Optional[datetime],
    end: datetime,
    db_url: Optional[str] = None,
    min_records: int = MIN_RECORDS,
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
    """
    url = db_url or settings.database_url

    conn = await asyncpg.connect(url)
    try:
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
