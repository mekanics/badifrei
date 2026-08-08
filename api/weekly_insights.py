"""Weekly insights cache: TTL, staleness, background refresh, compute."""

import logging
from datetime import datetime, timedelta, timezone

from api.catalog import ZURICH_TZ
from api.config import get_settings
from api.predictor import predictor

logger = logging.getLogger(__name__)

_DAY_DE_FULL = {
    0: "Montag",
    1: "Dienstag",
    2: "Mittwoch",
    3: "Donnerstag",
    4: "Freitag",
    5: "Samstag",
    6: "Sonntag",
}


def weekly_insights_ttl_seconds() -> int:
    return get_settings().weekly_insights_cache_ttl_seconds


# Compatibility alias for tests / callers that expect a module-level int.
# Re-read via property-style access in is_stale default; this value is set at
# import time and updated when tests clear settings + reload this module.
WEEKLY_INSIGHTS_CACHE_TTL_SECONDS: int = weekly_insights_ttl_seconds()


def is_stale(computed_at: datetime, ttl: int | None = None) -> bool:
    """Return True if the cache entry is at or beyond its TTL."""
    if ttl is None:
        ttl = weekly_insights_ttl_seconds()
    age = (datetime.now(timezone.utc) - computed_at).total_seconds()
    return age >= ttl


def _compute_weekly_insights(weekly_preds: list[list[float]]) -> dict | None:
    """Derive best/worst visiting time insights from a 7×24 prediction grid."""
    all_open = [
        (day, hour, v)
        for day, hours in enumerate(weekly_preds)
        for hour, v in enumerate(hours)
        if v > 0
    ]

    if not all_open:
        return {"has_data": False}

    quietest = min(all_open, key=lambda x: x[2])
    busiest = max(all_open, key=lambda x: x[2])

    day_avgs = []
    for day_idx, day_hours in enumerate(weekly_preds):
        open_vals = [v for v in day_hours if v > 0]
        if open_vals:
            day_avgs.append((day_idx, sum(open_vals) / len(open_vals)))

    quietest_day_idx = min(day_avgs, key=lambda x: x[1])[0] if day_avgs else quietest[0]

    weekday_vals = [v for day, h, v in all_open if day < 5]
    weekend_vals = [v for day, h, v in all_open if day >= 5]
    weekday_avg = sum(weekday_vals) / len(weekday_vals) if weekday_vals else None
    weekend_avg = sum(weekend_vals) / len(weekend_vals) if weekend_vals else None
    weekday_quieter = (
        (weekday_avg < weekend_avg) if (weekday_avg and weekend_avg) else None
    )

    return {
        "has_data": True,
        "quietest_day_name": _DAY_DE_FULL[quietest_day_idx],
        "quietest_hour": quietest[1],
        "quietest_hour_str": f"{quietest[1]:02d}:00",
        "peak_hour": busiest[1],
        "peak_hour_str": f"{busiest[1]:02d}:00",
        "weekday_quieter_than_weekend": weekday_quieter,
    }


async def refresh_weekly_insights(pool_uid: str, db_pool, app_state) -> None:
    """Background coroutine: compute the 168-hour weekly insights and store in cache.

    Errors are logged at WARNING level; the existing cache entry is left intact
    so stale data is still served rather than nothing.
    """
    try:
        today = datetime.now(tz=ZURICH_TZ).date()
        mon = today - timedelta(days=today.weekday())
        flat_hours = [
            datetime(
                (mon + timedelta(days=d)).year,
                (mon + timedelta(days=d)).month,
                (mon + timedelta(days=d)).day,
                h,
                0,
                0,
                tzinfo=ZURICH_TZ,
            )
            for d in range(7)
            for h in range(24)
        ]

        flat_preds = await predictor.predict_range_batch(pool_uid, flat_hours, db_pool)
        weekly_preds = [flat_preds[d * 24 : (d + 1) * 24] for d in range(7)]
        insights = _compute_weekly_insights(weekly_preds)

        app_state.weekly_insights_cache[pool_uid] = (
            insights,
            datetime.now(timezone.utc),
        )
        logger.debug("Weekly insights cache refreshed for pool %s", pool_uid)
    except Exception as exc:
        logger.warning(
            "Failed to refresh weekly insights for pool %s: %s", pool_uid, exc
        )
    finally:
        try:
            app_state.weekly_insights_inflight.discard(pool_uid)
        except Exception:
            pass


# Back-compat alias used by older call sites / tests
_refresh_weekly_insights = refresh_weekly_insights
