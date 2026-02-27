"""Database writer for pool occupancy data."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from collector.config import settings
from collector.ws_client import PoolReading

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the connection pool (singleton)."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def write_batch(readings: list[PoolReading], timestamp: Optional[datetime] = None) -> int:
    """
    Write a batch of pool readings to the database.

    - Skips pools with max_space == 0 (sensor offline)
    - Uses current UTC time if timestamp not provided
    - Returns number of records written
    """
    if not readings:
        return 0

    ts = timestamp or datetime.now(timezone.utc)

    # Filter out pools with no capacity data
    valid = [r for r in readings if r.maxspace > 0]

    if not valid:
        logger.debug("No valid readings to write (all maxspace=0)")
        return 0

    pool = await get_pool()

    records = [
        (ts, r.uid, r.name, r.currentfill, r.maxspace, r.freespace)
        for r in valid
    ]

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO pool_occupancy (time, pool_uid, pool_name, current_fill, max_space, free_space)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            records,
        )

    logger.debug(f"Wrote {len(records)} records at {ts.isoformat()}")
    return len(records)
