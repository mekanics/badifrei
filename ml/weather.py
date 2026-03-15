"""Open-Meteo weather fetcher with per-city support and TimescaleDB persistence."""
import asyncio
import datetime
import logging
import os
from collections.abc import Iterable
from typing import Any

import aiohttp
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# City coordinates (TASK-026)
# ---------------------------------------------------------------------------

# Maps city slug → (latitude, longitude) — city-centre approximations.
CITY_COORDS: dict[str, tuple[float, float]] = {
    "zurich":    (47.3769, 8.5417),
    "bern":      (46.9480, 7.4474),
    "adliswil":  (47.3090, 8.5245),
    "luzern":    (47.0502, 8.3093),
    "entfelden": (47.3916, 8.0541),
    "hunenberg": (47.1720, 8.4139),
    "rotkreuz":  (47.1410, 8.4314),
    "wengen":    (46.6058, 7.9238),
}

# Backward-compat aliases kept for callers that reference the old module-level names.
LAT = CITY_COORDS["zurich"][0]
LON = CITY_COORDS["zurich"][1]

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY_FIELDS = ["temperature_2m", "precipitation", "weathercode"]

# In-memory cache: (city, date) → pd.DataFrame (hot layer — avoids DB round-trips)
_cache: dict[tuple[str, datetime.date], pd.DataFrame] = {}

# ---------------------------------------------------------------------------
# DB helpers (TASK-023 / TASK-026)
# ---------------------------------------------------------------------------

_INSERT_SQL = """
INSERT INTO hourly_weather (city, date, hour, temperature_c, precipitation_mm, weathercode)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (city, date, hour) DO NOTHING
"""

_SELECT_SQL = """
SELECT date, hour, temperature_c, precipitation_mm, weathercode
FROM hourly_weather
WHERE date = ANY($1) AND city = $2
ORDER BY date, hour
"""

_TRUNCATE_SQL = "TRUNCATE TABLE hourly_weather"


async def _get_db_conn():
    """Return an asyncpg connection using DATABASE_URL env var.

    Callers are responsible for closing the connection.
    Kept thin so tests can mock it easily.
    """
    import asyncpg  # type: ignore

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Set it to a valid PostgreSQL connection string before running the ML pipeline."
        )
    return await asyncpg.connect(url)


async def _load_dates_from_db(
    conn, dates: list[datetime.date], city: str = "zurich"
) -> dict[datetime.date, pd.DataFrame]:
    """Query DB for rows belonging to *dates* and *city*.

    Returns a dict mapping date → DataFrame for dates that have rows in the
    DB for the given city.  Dates with zero rows are absent from the result.
    """
    if not dates:
        return {}

    rows = await conn.fetch(_SELECT_SQL, dates, city)
    if not rows:
        return {}

    df = pd.DataFrame(
        list(rows),
        columns=["date", "hour", "temperature_c", "precipitation_mm", "weathercode"],
    )

    result: dict[datetime.date, pd.DataFrame] = {}
    for date, group in df.groupby("date"):
        result[date] = group.reset_index(drop=True)

    return result


async def _persist_to_db(conn, df: pd.DataFrame, city: str = "zurich") -> None:
    """Write non-NaN rows from *df* to the ``hourly_weather`` table.

    Rows where **all three** weather value columns are NaN are skipped so we
    never poison the DB cache with fallback data.  Uses
    ``INSERT … ON CONFLICT (city, date, hour) DO NOTHING`` for idempotent writes.
    """
    weather_cols = ["temperature_c", "precipitation_mm", "weathercode"]
    # Keep rows where at least one weather column is not NaN
    valid = df.dropna(subset=weather_cols, how="all")
    if valid.empty:
        return

    records = [
        (
            city,
            row["date"] if isinstance(row["date"], datetime.date) else row["date"].date(),
            int(row["hour"]),
            None if pd.isna(row["temperature_c"]) else float(row["temperature_c"]),
            None if pd.isna(row["precipitation_mm"]) else float(row["precipitation_mm"]),
            None if pd.isna(row["weathercode"]) else int(row["weathercode"]),
        )
        for _, row in valid.iterrows()
    ]

    await conn.executemany(_INSERT_SQL, records)


# ---------------------------------------------------------------------------
# Open-Meteo helpers
# ---------------------------------------------------------------------------

def _nan_df() -> pd.DataFrame:
    """Return an empty NaN-filled DataFrame with expected columns (no date column)."""
    return pd.DataFrame({
        "hour": list(range(24)),
        "temperature_c": [np.nan] * 24,
        "precipitation_mm": [np.nan] * 24,
        "weathercode": [np.nan] * 24,
    })


def _select_url(date: datetime.date) -> str:
    """Select forecast or archive URL based on date."""
    today = datetime.date.today()
    # Archive has data up to ~5 days ago; use forecast for recent/future dates
    if date >= today - datetime.timedelta(days=5):
        return FORECAST_URL
    return ARCHIVE_URL


def _parse_response(data: dict[str, Any], date: datetime.date) -> pd.DataFrame:
    """Parse Open-Meteo hourly JSON response into a per-hour DataFrame."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    precip = hourly.get("precipitation", [])
    codes = hourly.get("weathercode", [])

    date_str = date.isoformat()
    rows = []
    for i, t in enumerate(times):
        if t.startswith(date_str):
            hour = int(t[11:13])
            rows.append({
                "date": date,
                "hour": hour,
                "temperature_c": temps[i] if i < len(temps) else np.nan,
                "precipitation_mm": precip[i] if i < len(precip) else np.nan,
                "weathercode": codes[i] if i < len(codes) else np.nan,
            })

    if not rows:
        logger.warning("No hourly data found for %s in response", date)
        return _nan_df()

    return pd.DataFrame(rows).sort_values("hour").reset_index(drop=True)


async def fetch_weather(date: datetime.date, city: str = "zurich") -> pd.DataFrame:
    """
    Fetch hourly weather data for the given city on the given date.

    Returns a DataFrame with columns:
        hour (0-23), temperature_c, precipitation_mm, weathercode

    Uses in-memory cache keyed by (city, date) so repeated calls don't re-fetch.
    Returns a NaN-filled DataFrame on any error or unknown city slug.
    """
    if city not in CITY_COORDS:
        logger.warning("Unknown city slug %r — returning NaN DataFrame", city)
        return _nan_df()

    cache_key = (city, date)
    if cache_key in _cache:
        logger.debug("Cache hit for weather city=%s date=%s", city, date)
        return _cache[cache_key]

    lat, lon = CITY_COORDS[city]
    url = _select_url(date)
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(HOURLY_FIELDS),
        "start_date": date.isoformat(),
        "end_date": date.isoformat(),
        "timezone": "UTC",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.error("Open-Meteo returned HTTP %s for city=%s date=%s", resp.status, city, date)
                    return _nan_df()
                data = await resp.json()
    except Exception as exc:
        logger.error("Failed to fetch weather for city=%s date=%s: %s", city, date, exc)
        return _nan_df()

    df = _parse_response(data, date)
    df = df.drop(columns=["date"], errors="ignore")
    _cache[cache_key] = df   # always WITHOUT date
    logger.info("Fetched weather for city=%s date=%s (%d rows)", city, date, len(df))
    return df


async def fetch_weather_batch(
    dates: "Iterable[datetime.date]",
    city: str = "zurich",
    max_concurrency: int = 10,
) -> pd.DataFrame:
    """Fetch weather for multiple dates and a specific city, using DB cache first.

    Layer order (fastest → slowest):
    1. In-memory ``_cache`` dict keyed by ``(city, date)`` — hot layer, no I/O.
    2. ``hourly_weather`` TimescaleDB table — persisted across process restarts.
    3. Open-Meteo HTTP API — only for truly missing (city, date) pairs.

    Fetched rows are persisted to DB (NaN fallback rows are **not** written).

    Returns a combined DataFrame with columns:
        date, hour (0-23), temperature_c, precipitation_mm, weathercode

    Args:
        dates: Iterable of calendar dates to fetch.
        city: City slug; must be a key in CITY_COORDS.  Defaults to "zurich"
            for backward compatibility.
        max_concurrency: Maximum simultaneous Open-Meteo requests.
    """
    if city not in CITY_COORDS:
        logger.warning("Unknown city slug %r — returning empty NaN DataFrame", city)
        return pd.DataFrame(
            columns=["date", "hour", "temperature_c", "precipitation_mm", "weathercode"]
        )

    unique_dates = sorted(set(dates))
    if not unique_dates:
        return pd.DataFrame(
            columns=["date", "hour", "temperature_c", "precipitation_mm", "weathercode"]
        )

    frames: list[pd.DataFrame] = []

    # --- Layer 1: in-memory cache ---
    mem_dates = [d for d in unique_dates if (city, d) in _cache]
    missing_after_mem = [d for d in unique_dates if (city, d) not in _cache]

    for d in mem_dates:
        df = _cache[(city, d)].copy()
        df["date"] = d
        frames.append(df)

    if not missing_after_mem:
        combined = pd.concat(frames, ignore_index=True)
        logger.info("All %d dates served from in-memory cache for city=%s", len(unique_dates), city)
        return combined

    # --- Layer 2: DB ---
    db_hit: dict[datetime.date, pd.DataFrame] = {}
    try:
        conn = await _get_db_conn()
        try:
            db_hit = await _load_dates_from_db(conn, missing_after_mem, city=city)
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning(
            "DB weather read failed (%s); falling back to HTTP for all missing dates", exc
        )

    for d, df in db_hit.items():
        # Populate in-memory cache so next call in this process is instant
        _cache[(city, d)] = df
        frames.append(df)

    missing_after_db = [d for d in missing_after_mem if d not in db_hit]

    if not missing_after_db:
        combined = pd.concat(frames, ignore_index=True)
        logger.info(
            "Weather served for city=%s: %d from mem cache, %d from DB; 0 from HTTP",
            city,
            len(mem_dates),
            len(db_hit),
        )
        return combined

    # --- Layer 3: Open-Meteo HTTP ---
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch_one(d: datetime.date) -> pd.DataFrame:
        async with semaphore:
            try:
                df = await fetch_weather(d, city=city)
                df = df.copy()
                df["date"] = d
                return df
            except Exception as exc:
                logger.warning("Weather batch fetch failed for city=%s date=%s: %s", city, d, exc)
                df = _nan_df()
                df["date"] = d
                return df

    fetched_frames = await asyncio.gather(*[_fetch_one(d) for d in missing_after_db])

    # Persist new rows to DB (skip NaN-only frames)
    try:
        conn = await _get_db_conn()
        try:
            for df in fetched_frames:
                await _persist_to_db(conn, df, city=city)
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("DB weather write failed (%s); rows not cached in DB", exc)

    frames.extend(fetched_frames)
    combined = pd.concat(frames, ignore_index=True)
    logger.info(
        "Weather fetched for city=%s: %d from mem, %d from DB, %d from HTTP (%d rows total)",
        city,
        len(mem_dates),
        len(db_hit),
        len(missing_after_db),
        len(combined),
    )
    return combined


def clear_cache() -> None:
    """Clear the in-memory weather cache (useful for testing).

    For DB cache truncation in test environments, use ``clear_cache_db()``
    with the ``WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR=true`` env var set.
    """
    _cache.clear()


async def clear_cache_db() -> None:
    """Async variant: clear in-memory cache AND truncate the DB table.

    DB truncation is guarded by the ``WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR``
    env flag to prevent accidental data loss in production.  Only intended
    for test environment cleanup.
    """
    _cache.clear()
    if os.getenv("WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR", "").lower() == "true":
        try:
            conn = await _get_db_conn()
            try:
                await conn.execute(_TRUNCATE_SQL)
                logger.info("Truncated hourly_weather table (test cleanup)")
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("Failed to truncate hourly_weather: %s", exc)
