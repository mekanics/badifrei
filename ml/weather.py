"""Open-Meteo weather fetcher with per-city support and TimescaleDB persistence."""

import asyncio
import datetime
import hashlib
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
    "zurich": (47.3769, 8.5417),
    "bern": (46.9480, 7.4474),
    "adliswil": (47.3090, 8.5245),
    "luzern": (47.0502, 8.3093),
    "entfelden": (47.3916, 8.0541),
    "hunenberg": (47.1720, 8.4139),
    "rotkreuz": (47.1410, 8.4314),
    "wengen": (46.6058, 7.9238),
}

# Backward-compat aliases kept for callers that reference the old module-level names.
LAT = CITY_COORDS["zurich"][0]
LON = CITY_COORDS["zurich"][1]

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY_FIELDS = ["temperature_2m", "precipitation", "weathercode"]

# Max age for a live WeatherHint row before the current UTC hour is re-fetched.
LIVE_WEATHER_MAX_AGE = datetime.timedelta(minutes=30)

# Max age for in-lag forecast days before ``fetch_weather_batch`` re-fetches.
# Between live TTL (30m) and retrain cadence (~8h); forecasts drift within the day.
FORECAST_WEATHER_MAX_AGE = datetime.timedelta(hours=6)

# Open-Meteo archive lag — must stay aligned with ``_select_url``.
ARCHIVE_LAG_DAYS = 5

SOURCE_FORECAST = "forecast"
SOURCE_ARCHIVE = "archive"
SOURCE_LIVE = "live"

# In-memory cache: (city, date) → pd.DataFrame (hot layer — avoids DB round-trips)
_cache: dict[tuple[str, datetime.date], pd.DataFrame] = {}
# When the mem frame was considered fresh (UTC); used for in-lag forecast TTL.
_cache_at: dict[tuple[str, datetime.date], datetime.datetime] = {}

# Serialize + pace Open-Meteo HTTP (free tier: ~1 concurrent request / IP).
# Override with OPEN_METEO_MIN_INTERVAL_S=0 in tests.
OPEN_METEO_MIN_INTERVAL_S = float(os.getenv("OPEN_METEO_MIN_INTERVAL_S", "0.35"))
_http_lock = asyncio.Lock()
_last_http_mono: float = 0.0


async def _open_meteo_get(
    url: str, params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """One in-flight Open-Meteo GET, with a minimum gap after the previous request."""
    global _last_http_mono
    async with _http_lock:
        loop = asyncio.get_running_loop()
        now = loop.time()
        wait = OPEN_METEO_MIN_INTERVAL_S - (now - _last_http_mono)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    status = resp.status
                    if status != 200:
                        return status, {}
                    data = await resp.json()
                    return status, data
        finally:
            _last_http_mono = loop.time()


# ---------------------------------------------------------------------------
# DB helpers (TASK-023 / TASK-026)
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
INSERT INTO hourly_weather (
    city, date, hour, temperature_c, precipitation_mm, weathercode, fetched_at, source
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (city, date, hour) DO UPDATE SET
    temperature_c = EXCLUDED.temperature_c,
    precipitation_mm = EXCLUDED.precipitation_mm,
    weathercode = EXCLUDED.weathercode,
    fetched_at = EXCLUDED.fetched_at,
    source = EXCLUDED.source
"""

# Forecast refresh: update drifting forecasts, never clobber archive or live hours.
_UPSERT_FORECAST_SQL = """
INSERT INTO hourly_weather (
    city, date, hour, temperature_c, precipitation_mm, weathercode, fetched_at, source
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (city, date, hour) DO UPDATE SET
    temperature_c = EXCLUDED.temperature_c,
    precipitation_mm = EXCLUDED.precipitation_mm,
    weathercode = EXCLUDED.weathercode,
    fetched_at = EXCLUDED.fetched_at,
    source = EXCLUDED.source
WHERE hourly_weather.source IS DISTINCT FROM 'archive'
  AND hourly_weather.source IS DISTINCT FROM 'live'
"""

# Aliases kept for tests / call sites that name the write policy explicitly.
_UPSERT_LIVE_SQL = _UPSERT_SQL
_UPSERT_ARCHIVE_SQL = _UPSERT_SQL

_SELECT_SQL = """
SELECT date, hour, temperature_c, precipitation_mm, weathercode, source, fetched_at
FROM hourly_weather
WHERE date = ANY($1) AND city = $2
ORDER BY date, hour
"""

_TRUNCATE_SQL = "TRUNCATE TABLE hourly_weather"


def is_live_weather_stale(
    fetched_at: datetime.datetime | None,
    *,
    now: datetime.datetime,
) -> bool:
    """True when a live hour row is missing a fetch time or older than the TTL."""
    if fetched_at is None:
        return True
    fetched = fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=datetime.timezone.utc)
    ref = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    return (ref - fetched.astimezone(ref.tzinfo)) >= LIVE_WEATHER_MAX_AGE


def _utc_today() -> datetime.date:
    """UTC calendar date — matches Open-Meteo / ``hourly_weather`` UTC keys."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def is_archive_eligible(
    date: datetime.date,
    *,
    today: datetime.date | None = None,
) -> bool:
    """True when Open-Meteo archive should be used (``date < today - lag``).

    Default *today* is the UTC calendar date so eligibility aligns with
    UTC-keyed ``hourly_weather`` rows (not the process local timezone).
    """
    ref = today if today is not None else _utc_today()
    return date < ref - datetime.timedelta(days=ARCHIVE_LAG_DAYS)


def _is_archive_backed(df: pd.DataFrame) -> bool:
    """True when a full 24-hour day is present and every hour is ``source=archive``.

    Incomplete days (missing hours) are treated as dirty so the next batch
    re-fetches archive rather than training on a partial freeze.
    """
    if df is None or df.empty:
        return False
    if "source" not in df.columns or "hour" not in df.columns:
        return False
    hours = set(int(h) for h in df["hour"].tolist())
    if hours != set(range(24)):
        return False
    return bool((df["source"] == SOURCE_ARCHIVE).all())


def _is_forecast_day_fresh(
    df: pd.DataFrame,
    *,
    now: datetime.datetime,
) -> bool:
    """True when an in-lag day has ``fetched_at`` on every hour within the TTL.

    Null ``fetched_at`` (legacy write-once rows) is always stale so the next
    batch picks up a newer Open-Meteo forecast.
    """
    if df is None or df.empty:
        return False
    if _is_archive_backed(df):
        return True
    if "fetched_at" not in df.columns:
        return False
    stamps = df["fetched_at"]
    if stamps.isna().any():
        return False
    oldest = stamps.min()
    if oldest is None or (isinstance(oldest, float) and np.isnan(oldest)):
        return False
    if not isinstance(oldest, datetime.datetime):
        return False
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=datetime.timezone.utc)
    ref = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    return (ref - oldest.astimezone(ref.tzinfo)) < FORECAST_WEATHER_MAX_AGE


def _remember_cache(
    city: str,
    date: datetime.date,
    df: pd.DataFrame,
    *,
    fetched_at: datetime.datetime | None = None,
) -> None:
    """Store a day frame in the mem cache with a freshness timestamp."""
    cached = df.drop(columns=["date", "source", "fetched_at"], errors="ignore")
    _cache[(city, date)] = cached
    if fetched_at is None:
        fetched_at = datetime.datetime.now(datetime.timezone.utc)
    elif fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=datetime.timezone.utc)
    _cache_at[(city, date)] = fetched_at


def _mem_forecast_fresh(
    city: str,
    date: datetime.date,
    *,
    now: datetime.datetime,
) -> bool:
    """True when mem cache has this in-lag day and its cache timestamp is fresh."""
    if (city, date) not in _cache:
        return False
    cached_at = _cache_at.get((city, date))
    if cached_at is None:
        return False
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=datetime.timezone.utc)
    ref = now if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    return (ref - cached_at.astimezone(ref.tzinfo)) < FORECAST_WEATHER_MAX_AGE


def _live_advisory_key(city: str, date: datetime.date, hour: int) -> int:
    """Stable positive int32 key for ``pg_advisory_lock`` (city, UTC date, hour)."""
    digest = hashlib.md5(
        f"{city}:{date.isoformat()}:{hour}".encode(),
        usedforsecurity=False,
    ).hexdigest()
    return int(digest[:8], 16) % (2**31)


def _frame_for_return(df: pd.DataFrame, date: datetime.date) -> pd.DataFrame:
    """Normalize a day frame for callers (date column, no source/fetched_at)."""
    out = df.copy()
    out["date"] = date
    return out.drop(columns=["source", "fetched_at"], errors="ignore")


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
    Frames include a ``source`` column when present in the table.
    """
    if not dates:
        return {}

    rows = await conn.fetch(_SELECT_SQL, dates, city)
    if not rows:
        return {}

    # asyncpg.Record iterates values (not keys); list(rows) → integer columns and
    # KeyError('date') on groupby. Always materialize mappings first.
    df = pd.DataFrame([dict(row) for row in rows])
    result: dict[datetime.date, pd.DataFrame] = {}
    for day, group in df.groupby("date"):
        result[day] = group.reset_index(drop=True)

    return result


def _weather_records(
    df: pd.DataFrame,
    city: str,
    *,
    include_fetched_at: bool,
    source: str | None = None,
) -> list[tuple]:
    """Build DB tuples from a weather DataFrame (skip all-NaN rows)."""
    weather_cols = ["temperature_c", "precipitation_mm", "weathercode"]
    valid = df.dropna(subset=weather_cols, how="all")
    if valid.empty:
        return []

    fetched_at = datetime.datetime.now(datetime.timezone.utc)
    records: list[tuple] = []
    for _, row in valid.iterrows():
        day = row["date"]
        if not isinstance(day, datetime.date):
            day = day.date()
        temp = None if pd.isna(row["temperature_c"]) else float(row["temperature_c"])
        precip = (
            None if pd.isna(row["precipitation_mm"]) else float(row["precipitation_mm"])
        )
        code = None if pd.isna(row["weathercode"]) else int(row["weathercode"])
        if include_fetched_at:
            records.append(
                (city, day, int(row["hour"]), temp, precip, code, fetched_at, source)
            )
        else:
            records.append((city, day, int(row["hour"]), temp, precip, code))
    return records


async def _persist_to_db(conn, df: pd.DataFrame, city: str = "zurich") -> None:
    """Upsert forecast rows; never clobbers ``archive`` or ``live`` hours."""
    records = _weather_records(
        df, city, include_fetched_at=True, source=SOURCE_FORECAST
    )
    if not records:
        return
    await conn.executemany(_UPSERT_FORECAST_SQL, records)


async def _upsert_day_to_db(
    conn,
    df: pd.DataFrame,
    city: str,
    *,
    source: str,
) -> None:
    """Upsert a full day with ``source`` / ``fetched_at`` (archive or shared helper)."""
    records = _weather_records(df, city, include_fetched_at=True, source=source)
    if not records:
        return
    await conn.executemany(_UPSERT_SQL, records)


def _coerce_weather_cell(value):
    """Normalize pandas/DB cells: NaN → None."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and np.isnan(value):
            return None
    except TypeError:
        pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


async def refresh_live_hour(
    city: str,
    when: datetime.datetime,
) -> dict[str, Any] | None:
    """Force-fetch Open-Meteo for the UTC hour of *when* and upsert that hour.

    Uses a Postgres advisory lock on ``(city, UTC date, hour)`` so concurrent
    API workers (and coroutines) single-flight the Open-Meteo call: waiters
    re-check ``fetched_at`` after the lock and skip HTTP when another worker
    already refreshed. Sets ``source='live'``.

    Returns ``{temperature_c, precipitation_mm, weathercode, fetched_at}`` or
    ``None`` on unknown city / empty hour / all-NaN values.
    """
    if city not in CITY_COORDS:
        logger.warning("refresh_live_hour: unknown city slug %r", city)
        return None
    if when.tzinfo is None:
        logger.warning("refresh_live_hour: naive datetime rejected for city=%s", city)
        return None

    when_utc = when.astimezone(datetime.timezone.utc)
    lookup_date = when_utc.date()
    lookup_hour = when_utc.hour
    lock_key = _live_advisory_key(city, lookup_date, lookup_hour)

    try:
        conn = await _get_db_conn()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "refresh_live_hour: DB connect failed for city=%s (%s)", city, exc
        )
        return None

    try:
        await conn.execute("SELECT pg_advisory_lock($1)", lock_key)
        try:
            row = await conn.fetchrow(
                """
                SELECT temperature_c, precipitation_mm, weathercode, fetched_at
                FROM hourly_weather
                WHERE city = $1 AND date = $2::date AND hour = $3
                """,
                city,
                lookup_date,
                lookup_hour,
            )
            if row is not None and not is_live_weather_stale(
                row["fetched_at"], now=when_utc
            ):
                return {
                    "temperature_c": _coerce_weather_cell(row["temperature_c"]),
                    "precipitation_mm": _coerce_weather_cell(row["precipitation_mm"]),
                    "weathercode": (
                        int(row["weathercode"])
                        if _coerce_weather_cell(row["weathercode"]) is not None
                        else None
                    ),
                    "fetched_at": row["fetched_at"],
                }

            # Bust mem cache before fetch so we do not serve a stale day frame.
            _cache.pop((city, lookup_date), None)
            _cache_at.pop((city, lookup_date), None)

            df = await fetch_weather(lookup_date, city=city)
            match = df[df["hour"] == lookup_hour]
            if match.empty:
                logger.warning(
                    "refresh_live_hour: no hour=%s row for city=%s date=%s",
                    lookup_hour,
                    city,
                    lookup_date,
                )
                return None

            rec = match.iloc[0]
            temp = _coerce_weather_cell(rec["temperature_c"])
            precip = _coerce_weather_cell(rec["precipitation_mm"])
            code = _coerce_weather_cell(rec["weathercode"])
            if temp is None and precip is None and code is None:
                return None

            temp_f = float(temp) if temp is not None else None
            precip_f = float(precip) if precip is not None else None
            code_i = int(code) if code is not None else None
            fetched_at = datetime.datetime.now(datetime.timezone.utc)

            await conn.execute(
                _UPSERT_LIVE_SQL,
                city,
                lookup_date,
                lookup_hour,
                temp_f,
                precip_f,
                code_i,
                fetched_at,
                SOURCE_LIVE,
            )

            _cache.pop((city, lookup_date), None)
            _cache_at.pop((city, lookup_date), None)
            return {
                "temperature_c": temp_f,
                "precipitation_mm": precip_f,
                "weathercode": code_i,
                "fetched_at": fetched_at,
            }
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", lock_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "refresh_live_hour: failed for city=%s (%s)",
            city,
            exc,
        )
        return None
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Open-Meteo helpers
# ---------------------------------------------------------------------------


def _nan_df() -> pd.DataFrame:
    """Return an empty NaN-filled DataFrame with expected columns (no date column)."""
    return pd.DataFrame(
        {
            "hour": list(range(24)),
            "temperature_c": [np.nan] * 24,
            "precipitation_mm": [np.nan] * 24,
            "weathercode": [np.nan] * 24,
        }
    )


def _select_url(date: datetime.date) -> str:
    """Select forecast or archive URL based on date."""
    if is_archive_eligible(date):
        return ARCHIVE_URL
    return FORECAST_URL


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
            rows.append(
                {
                    "date": date,
                    "hour": hour,
                    "temperature_c": temps[i] if i < len(temps) else np.nan,
                    "precipitation_mm": precip[i] if i < len(precip) else np.nan,
                    "weathercode": codes[i] if i < len(codes) else np.nan,
                }
            )

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
        status, data = await _open_meteo_get(url, params)
        if status != 200:
            logger.error(
                "Open-Meteo returned HTTP %s for city=%s date=%s",
                status,
                city,
                date,
            )
            return _nan_df()
    except Exception as exc:
        logger.error("Failed to fetch weather for city=%s date=%s: %s", city, date, exc)
        return _nan_df()

    df = _parse_response(data, date)
    df = df.drop(columns=["date"], errors="ignore")
    _remember_cache(city, date, df)  # hot layer + forecast TTL stamp
    logger.info("Fetched weather for city=%s date=%s (%d rows)", city, date, len(df))
    return df


async def fetch_weather_batch(
    dates: "Iterable[datetime.date]",
    city: str = "zurich",
    max_concurrency: int = 1,
) -> pd.DataFrame:
    """Fetch weather for multiple dates and a specific city.

    Policy:
    - **Archive-eligible** dates (``date < today - ARCHIVE_LAG_DAYS``) with
      ``source != 'archive'`` (including legacy null) are force-refetched from
      the archive API and upserted (concurrency clamped to 1).
    - Archive-backed days are served from DB (or mem after a prior load).
    - Recent / in-lag dates refresh from the forecast API when missing or when
      ``fetched_at`` is older than ``FORECAST_WEATHER_MAX_AGE`` (or null).
      Forecast upserts never clobber ``archive`` or ``live`` hours.

    Returns a combined DataFrame with columns:
        date, hour (0-23), temperature_c, precipitation_mm, weathercode
    """
    empty_cols = [
        "date",
        "hour",
        "temperature_c",
        "precipitation_mm",
        "weathercode",
    ]
    if city not in CITY_COORDS:
        logger.warning("Unknown city slug %r — returning empty NaN DataFrame", city)
        return pd.DataFrame(columns=empty_cols)

    unique_dates = sorted(set(dates))
    if not unique_dates:
        return pd.DataFrame(columns=empty_cols)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    frames: list[pd.DataFrame] = []
    need_archive: list[datetime.date] = []
    need_forecast: list[datetime.date] = []
    serve_recent_mem: list[datetime.date] = []

    # Partition: fresh in-lag mem hits can skip DB; everything else consults DB.
    dates_needing_db = [
        d
        for d in unique_dates
        if is_archive_eligible(d) or not _mem_forecast_fresh(city, d, now=now_utc)
    ]
    recent_mem = [
        d
        for d in unique_dates
        if not is_archive_eligible(d) and _mem_forecast_fresh(city, d, now=now_utc)
    ]
    serve_recent_mem.extend(recent_mem)

    db_hit: dict[datetime.date, pd.DataFrame] = {}
    if dates_needing_db:
        try:
            conn = await _get_db_conn()
            try:
                db_hit = await _load_dates_from_db(conn, dates_needing_db, city=city)
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DB weather read failed (%s); falling back to HTTP for missing dates",
                exc,
            )

    for d in unique_dates:
        if d in serve_recent_mem:
            continue
        if is_archive_eligible(d):
            if d in db_hit and _is_archive_backed(db_hit[d]):
                frames.append(_frame_for_return(db_hit[d], d))
                _remember_cache(city, d, db_hit[d], fetched_at=now_utc)
            else:
                need_archive.append(d)
                _cache.pop((city, d), None)
                _cache_at.pop((city, d), None)
        elif d in db_hit and _is_forecast_day_fresh(db_hit[d], now=now_utc):
            frames.append(_frame_for_return(db_hit[d], d))
            oldest = db_hit[d]["fetched_at"].min()
            _remember_cache(city, d, db_hit[d], fetched_at=oldest)
        else:
            # Missing, legacy null fetched_at, or older than forecast TTL.
            need_forecast.append(d)
            _cache.pop((city, d), None)
            _cache_at.pop((city, d), None)

    for d in serve_recent_mem:
        df = _cache[(city, d)].copy()
        frames.append(_frame_for_return(df, d))

    # Archive refresh — one concurrent HTTP request; one DB connection for upserts.
    if need_archive:
        archive_frames: list[pd.DataFrame] = []
        archive_conn = None
        try:
            archive_conn = await _get_db_conn()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Archive weather DB connect failed for city=%s (%s); "
                "serving existing rows where available",
                city,
                exc,
            )

        try:
            for d in need_archive:
                try:
                    _cache.pop((city, d), None)
                    _cache_at.pop((city, d), None)
                    df = await fetch_weather(d, city=city)
                    df = df.copy()
                    df["date"] = d
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Archive weather fetch failed for city=%s date=%s: %s",
                        city,
                        d,
                        exc,
                    )
                    if d in db_hit:
                        frames.append(_frame_for_return(db_hit[d], d))
                    else:
                        fallback = _nan_df()
                        fallback["date"] = d
                        frames.append(fallback)
                    continue

                weather_cols = ["temperature_c", "precipitation_mm", "weathercode"]
                if df.dropna(subset=weather_cols, how="all").empty:
                    if d in db_hit:
                        logger.warning(
                            "Archive fetch empty for city=%s date=%s; keeping DB row",
                            city,
                            d,
                        )
                        frames.append(_frame_for_return(db_hit[d], d))
                    else:
                        frames.append(
                            df.drop(columns=["source", "fetched_at"], errors="ignore")
                        )
                    continue

                if archive_conn is not None:
                    try:
                        await _upsert_day_to_db(
                            archive_conn, df, city, source=SOURCE_ARCHIVE
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Archive weather upsert failed for city=%s date=%s: %s",
                            city,
                            d,
                            exc,
                        )

                _cache.pop((city, d), None)
                _cache_at.pop((city, d), None)
                out = df.drop(columns=["source", "fetched_at"], errors="ignore")
                archive_frames.append(out)
                frames.append(out)
        finally:
            if archive_conn is not None:
                await archive_conn.close()

        logger.info(
            "Archive-refreshed %d date(s) for city=%s",
            len(archive_frames),
            city,
        )

    # Forecast HTTP for recent missing / stale-in-lag dates.
    if need_forecast:
        semaphore = asyncio.Semaphore(max_concurrency)
        forecast_ok: dict[datetime.date, pd.DataFrame] = {}
        weather_cols = ["temperature_c", "precipitation_mm", "weathercode"]

        async def _fetch_one(d: datetime.date) -> pd.DataFrame:
            async with semaphore:
                try:
                    df = await fetch_weather(d, city=city)
                    df = df.copy()
                    df["date"] = d
                    if df.dropna(subset=weather_cols, how="all").empty:
                        if d in db_hit:
                            logger.warning(
                                "Forecast fetch empty for city=%s date=%s; "
                                "keeping DB row",
                                city,
                                d,
                            )
                            return _frame_for_return(db_hit[d], d)
                        return df
                    forecast_ok[d] = df
                    return df
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Weather batch fetch failed for city=%s date=%s: %s",
                        city,
                        d,
                        exc,
                    )
                    if d in db_hit:
                        return _frame_for_return(db_hit[d], d)
                    df = _nan_df()
                    df["date"] = d
                    return df

        fetched_frames = await asyncio.gather(*[_fetch_one(d) for d in need_forecast])
        try:
            conn = await _get_db_conn()
            try:
                for d, df in forecast_ok.items():
                    await _persist_to_db(conn, df, city=city)
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB weather write failed (%s); rows not cached in DB", exc)

        fetched_at = datetime.datetime.now(datetime.timezone.utc)
        for df in fetched_frames:
            d = df["date"].iloc[0] if "date" in df.columns and len(df) else None
            out = df.drop(columns=["source", "fetched_at"], errors="ignore")
            frames.append(out)
            if d is not None and d in forecast_ok:
                _remember_cache(city, d, out, fetched_at=fetched_at)

    if not frames:
        return pd.DataFrame(columns=empty_cols)

    combined = pd.concat(frames, ignore_index=True)
    # Stable column set for callers / tests.
    for col in empty_cols:
        if col not in combined.columns:
            combined[col] = np.nan
    combined = combined[empty_cols]
    logger.info(
        "Weather batch city=%s: %d dates, %d archive refresh, %d forecast fetch (%d rows)",
        city,
        len(unique_dates),
        len(need_archive),
        len(need_forecast),
        len(combined),
    )
    return combined


def clear_cache() -> None:
    """Clear the in-memory weather cache (useful for testing).

    For DB cache truncation in test environments, use ``clear_cache_db()``
    with the ``WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR=true`` env var set.
    """
    _cache.clear()
    _cache_at.clear()


async def clear_cache_db() -> None:
    """Async variant: clear in-memory cache AND truncate the DB table.

    DB truncation is guarded by the ``WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR``
    env flag to prevent accidental data loss in production.  Only intended
    for test environment cleanup.
    """
    _cache.clear()
    _cache_at.clear()
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
