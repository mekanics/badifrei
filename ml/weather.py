"""Open-Meteo weather fetcher for Zürich with in-memory caching."""
import datetime
import logging
from typing import Any

import aiohttp
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Zürich coordinates
LAT = 47.3769
LON = 8.5417

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY_FIELDS = ["temperature_2m", "precipitation", "weathercode"]

# In-memory cache: date → pd.DataFrame
_cache: dict[datetime.date, pd.DataFrame] = {}


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


async def fetch_weather(date: datetime.date) -> pd.DataFrame:
    """
    Fetch hourly weather data for Zürich on the given date.

    Returns a DataFrame with columns:
        hour (0-23), temperature_c, precipitation_mm, weathercode

    Uses in-memory cache so repeated calls for the same date don't re-fetch.
    Returns a NaN-filled DataFrame on any error.
    """
    if date in _cache:
        logger.debug("Cache hit for weather date %s", date)
        return _cache[date]

    url = _select_url(date)
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": ",".join(HOURLY_FIELDS),
        "start_date": date.isoformat(),
        "end_date": date.isoformat(),
        "timezone": "UTC",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.error("Open-Meteo returned HTTP %s for date %s", resp.status, date)
                    return _nan_df()
                data = await resp.json()
    except Exception as exc:
        logger.error("Failed to fetch weather for %s: %s", date, exc)
        return _nan_df()

    df = _parse_response(data, date)
    _cache[date] = df
    logger.info("Fetched weather for %s (%d rows)", date, len(df))
    return df.drop(columns=["date"], errors="ignore")


async def fetch_weather_batch(
    dates: "Iterable[datetime.date]",
    max_concurrency: int = 10,
) -> pd.DataFrame:
    """Fetch weather for multiple dates concurrently.

    Returns a combined DataFrame with columns:
        date, hour (0-23), temperature_c, precipitation_mm, weathercode

    Useful at training time when you need weather for an entire historical dataset.
    Dates already in the in-memory cache are served without network I/O.
    Failed fetches are replaced with NaN rows (graceful degradation).

    Args:
        dates: Iterable of calendar dates to fetch.
        max_concurrency: Maximum simultaneous Open-Meteo requests.
    """
    import asyncio
    from collections.abc import Iterable as _Iterable

    unique_dates = sorted(set(dates))
    if not unique_dates:
        return pd.DataFrame(
            columns=["date", "hour", "temperature_c", "precipitation_mm", "weathercode"]
        )

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch_one(d: datetime.date) -> pd.DataFrame:
        async with semaphore:
            try:
                df = await fetch_weather(d)
                df = df.copy()
                df["date"] = d
                return df
            except Exception as exc:
                logger.warning("Weather batch fetch failed for %s: %s", d, exc)
                df = _nan_df()
                df["date"] = d
                return df

    frames = await asyncio.gather(*[_fetch_one(d) for d in unique_dates])
    combined = pd.concat(frames, ignore_index=True)
    logger.info(
        "Fetched weather for %d dates (%d rows total)", len(unique_dates), len(combined)
    )
    return combined


def clear_cache() -> None:
    """Clear the in-memory weather cache (useful for testing)."""
    _cache.clear()
