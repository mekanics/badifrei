"""Occupancy / status snapshots for /api/current and Markdown twins."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from api.catalog import ZURICH_TZ, get_pools
from api.prediction_days import _schedule_for_pool
from api.water_temperature import fresh_water_temp
from api.weather_display import weather_condition

if TYPE_CHECKING:
    from ml.opening_hours import Observation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LatestStatus:
    """One pool_status row: Observation for hours resolution + raw water temp."""

    observation: Observation | None
    water_temp_c: float | None
    observed_at: datetime | None
    source_modified_at: datetime | None


def _compute_pool_is_open(
    pool: dict,
    now_zurich: datetime,
    observation=None,
    weather=None,
) -> dict:
    """Compute is_open status for a pool given current Zürich time."""
    from ml.opening_hours import (
        resolve,
        resolution_to_api_dict,
        use_observed_override,
        _legacy_to_schedule,
    )

    schedule = _schedule_for_pool(pool)
    if schedule is None:
        oh = pool.get("opening_hours")
        if not oh:
            return {
                "is_open": True,
                "next_open": None,
                "opens_seasonal": None,
                "state": "unknown",
                "reason": None,
                "confidence": "unverified",
            }
        schedule = _legacy_to_schedule(pool.get("uid", "unknown"), oh)

    obs = observation if use_observed_override() else None
    return resolution_to_api_dict(
        resolve(schedule, now_zurich, observation=obs, weather=weather)
    )


def _coerce_weather_value(value):
    """Normalize DB/pandas weather cells: NaN → None."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
    except TypeError:
        pass
    return value


def _weather_hint_from_values(temp, precip, code):
    """Build WeatherHint or None when every field is missing."""
    from ml.opening_hours import WeatherHint

    temp = _coerce_weather_value(temp)
    precip = _coerce_weather_value(precip)
    code = _coerce_weather_value(code)
    if temp is None and precip is None and code is None:
        return None
    return WeatherHint(
        temperature_c=float(temp) if temp is not None else None,
        precipitation_mm=float(precip) if precip is not None else None,
        weathercode=int(code) if code is not None else None,
    )


async def _fetch_latest_status(db_pool, pool_uid: str) -> LatestStatus | None:
    """Latest pool_status row: Observation + water temp from a single query."""
    try:
        from ml.opening_hours import observation_from_status_text

        srow = await db_pool.fetchrow(
            """
            SELECT status_text, water_temp_c, source_modified_at, observed_at
            FROM pool_status
            WHERE pool_uid = $1
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            pool_uid,
        )
        if srow is None:
            return None
        return LatestStatus(
            observation=observation_from_status_text(
                srow["status_text"],
                observed_at=srow["observed_at"],
                source_modified_at=srow["source_modified_at"],
            ),
            water_temp_c=srow["water_temp_c"],
            observed_at=srow["observed_at"],
            source_modified_at=srow["source_modified_at"],
        )
    except Exception:  # noqa: BLE001
        return None


async def _fetch_latest_observation(db_pool, pool_uid: str):
    """Latest Baditicker Observation for one pool, or None if unavailable."""
    status = await _fetch_latest_status(db_pool, pool_uid)
    return status.observation if status is not None else None


def _coerce_live_max_space(value) -> int | None:
    """Positive CrowdMonitor max_space for SEO/UI, else None."""
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


async def _latest_max_space(db_pool, pool_uid: str) -> int | None:
    """Latest CrowdMonitor max_space for a pool (source of truth for capacity)."""
    if db_pool is None:
        return None
    try:
        row = await db_pool.fetchrow(
            """
            SELECT max_space
            FROM pool_occupancy
            WHERE pool_uid = $1 AND max_space > 0
            ORDER BY time DESC
            LIMIT 1
            """,
            pool_uid,
        )
        if row is None:
            return None
        return _coerce_live_max_space(row["max_space"])
    except Exception:  # noqa: BLE001
        return None


async def _fetch_city_weather_hints(
    db_pool,
    now_zurich: datetime,
    cities: Iterable[str] | None = None,
) -> dict:
    """Load current-hour WeatherHint per city, fetching when the cache is empty."""
    from ml.weather import CITY_COORDS, fetch_weather_batch

    now_utc = now_zurich.astimezone(timezone.utc)
    lookup_date = now_utc.date()
    lookup_hour = now_utc.hour

    wanted = {c for c in (cities or []) if c in CITY_COORDS}

    hints: dict = {}
    try:
        rows = await db_pool.fetch(
            """
            SELECT city, temperature_c, precipitation_mm, weathercode
            FROM hourly_weather
            WHERE date = $1::date AND hour = $2
            """,
            lookup_date,
            lookup_hour,
        )
        for row in rows:
            city = row["city"]
            if wanted and city not in wanted:
                continue
            hint = _weather_hint_from_values(
                row["temperature_c"],
                row["precipitation_mm"],
                row["weathercode"],
            )
            if hint is not None:
                hints[city] = hint
    except Exception as exc:  # noqa: BLE001
        logger.warning("hourly_weather lookup failed: %s", exc)

    for city in sorted(wanted - set(hints.keys())):
        try:
            df = await fetch_weather_batch([lookup_date], city=city)
        except Exception as exc:  # noqa: BLE001
            logger.warning("weather ensure-fetch failed for city=%s: %s", city, exc)
            continue
        if df is None or getattr(df, "empty", True):
            continue
        match = df[df["hour"] == lookup_hour]
        if match.empty:
            continue
        rec = match.iloc[0]
        hint = _weather_hint_from_values(
            rec["temperature_c"],
            rec["precipitation_mm"],
            rec["weathercode"],
        )
        if hint is not None:
            hints[city] = hint
    return hints


def _merge_current_pool_items(
    pools: list[dict],
    *,
    occupancy_by_uid: dict,
    observations: dict,
    weather_by_city: dict,
    now_zurich: datetime,
    water_temps: dict | None = None,
    compute_status=_compute_pool_is_open,
) -> list[dict]:
    """One /api/current row per known pool; occupancy fields null when absent."""
    water_temps = water_temps or {}
    result: list[dict] = []
    for pool in pools:
        uid = pool["uid"]
        occ = occupancy_by_uid.get(uid)
        if occ is not None:
            item = dict(occ)
            item["pool_uid"] = uid
        else:
            item = {
                "pool_uid": uid,
                "current_fill": None,
                "max_space": None,
                "free_space": None,
                "occupancy_pct": None,
                "time": None,
            }
        city = pool.get("city", "zurich")
        hint = weather_by_city.get(city)
        status = compute_status(
            pool,
            now_zurich,
            observation=observations.get(uid),
            weather=hint,
        )
        item["is_open"] = status["is_open"]
        item["next_open"] = status["next_open"]
        item["opens_seasonal"] = status["opens_seasonal"]
        item["state"] = status.get("state")
        item["reason"] = status.get("reason")
        item["confidence"] = status.get("confidence")
        item["water_temp_c"] = water_temps.get(uid)
        air = hint.temperature_c if hint is not None else None
        code = hint.weathercode if hint is not None else None
        item["air_temp_c"] = air
        item["weathercode"] = code
        condition = weather_condition(code)
        item["condition_label"] = condition[0] if condition else None
        item["condition_emoji"] = condition[1] if condition else None
        result.append(item)
    return result


async def load_current_snapshot(db_pool) -> list[dict]:
    """One occupancy+status row per known pool (used by /api/current).

    Returns [] when the DB pool is unavailable or the query fails.
    """
    now_zurich = datetime.now(ZURICH_TZ)
    pools = get_pools()

    if db_pool is None:
        return []
    try:
        rows = await db_pool.fetch(
            """
            SELECT DISTINCT ON (pool_uid)
                pool_uid, current_fill, max_space, free_space,
                ROUND((current_fill::numeric / NULLIF(max_space, 0)) * 100) AS occupancy_pct,
                time
            FROM pool_occupancy
            ORDER BY pool_uid, time DESC
            """
        )
        occupancy_by_uid = {row["pool_uid"]: dict(row) for row in rows}

        observations: dict = {}
        water_temps: dict = {}
        try:
            from ml.opening_hours import observation_from_status_text

            status_rows = await db_pool.fetch(
                """
                SELECT DISTINCT ON (pool_uid)
                    pool_uid, status_text, water_temp_c,
                    source_modified_at, observed_at
                FROM pool_status
                ORDER BY pool_uid, observed_at DESC
                """
            )
            for srow in status_rows:
                uid = srow["pool_uid"]
                observations[uid] = observation_from_status_text(
                    srow["status_text"],
                    observed_at=srow["observed_at"],
                    source_modified_at=srow["source_modified_at"],
                )
                gated = fresh_water_temp(
                    srow["water_temp_c"],
                    observed_at=srow["observed_at"],
                    source_modified_at=srow["source_modified_at"],
                    now=now_zurich,
                )
                if gated is not None:
                    water_temps[uid] = gated
        except Exception:
            observations = {}
            water_temps = {}

        cities = {p.get("city", "zurich") for p in pools}
        weather_by_city = await _fetch_city_weather_hints(
            db_pool, now_zurich, cities=cities
        )

        return _merge_current_pool_items(
            pools,
            occupancy_by_uid=occupancy_by_uid,
            observations=observations,
            weather_by_city=weather_by_city,
            now_zurich=now_zurich,
            water_temps=water_temps,
        )
    except Exception:
        return []


async def load_pool_snapshot(db_pool, pool: dict) -> dict | None:
    """Occupancy+status for a single pool (Markdown twins — avoid full-site scan)."""
    now_zurich = datetime.now(ZURICH_TZ)
    if db_pool is None:
        return None
    pool_uid = pool["uid"]
    try:
        row = await db_pool.fetchrow(
            """
            SELECT pool_uid, current_fill, max_space, free_space,
                   ROUND((current_fill::numeric / NULLIF(max_space, 0)) * 100)
                       AS occupancy_pct,
                   time
            FROM pool_occupancy
            WHERE pool_uid = $1
            ORDER BY time DESC
            LIMIT 1
            """,
            pool_uid,
        )
        occupancy_by_uid = {pool_uid: dict(row)} if row is not None else {}
        latest = await _fetch_latest_status(db_pool, pool_uid)
        observations = (
            {pool_uid: latest.observation}
            if latest is not None and latest.observation is not None
            else {}
        )
        water_temps: dict = {}
        if latest is not None:
            gated = fresh_water_temp(
                latest.water_temp_c,
                observed_at=latest.observed_at,
                source_modified_at=latest.source_modified_at,
                now=now_zurich,
            )
            if gated is not None:
                water_temps[pool_uid] = gated
        city = pool.get("city", "zurich")
        weather_by_city = await _fetch_city_weather_hints(
            db_pool, now_zurich, cities={city}
        )
        items = _merge_current_pool_items(
            [pool],
            occupancy_by_uid=occupancy_by_uid,
            observations=observations,
            weather_by_city=weather_by_city,
            now_zurich=now_zurich,
            water_temps=water_temps,
        )
        return items[0] if items else None
    except Exception:
        logger.warning(
            "Failed to load single-pool snapshot for Markdown uid=%s",
            pool_uid,
            exc_info=True,
        )
        return None
