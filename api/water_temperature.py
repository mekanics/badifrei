"""Water-temperature freshness gates for the detail-page temperature display.

Two gates must both pass before a Baditicker water temperature is shown:

1. ``observed_at`` within ``DEFAULT_OBSERVATION_MAX_AGE`` (60 min) — our
   collector is alive and still sees the value in the feed.
2. ``source_modified_at`` within ``WATER_TEMP_MAX_SOURCE_AGE`` (7 days) — the
   city still maintains the value; expires off-season readings.

Do NOT collapse this to a single short ``source_modified_at`` cutoff: measured
Baditicker update gaps reach 23.4 h in peak season (avg 6–12 h), so a short
cutoff false-hides live temperatures. Distinct from open/closed Observation
eligibility in ``ml.opening_hours``: that path still requires fresh
``observed_at``, but also requires ``source_modified_at`` at or after today's
first Guaranteed open (same-day cycle), not a 7-day age window.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ml.opening_hours import DEFAULT_OBSERVATION_MAX_AGE, ZURICH_TZ

WATER_TEMP_MAX_SOURCE_AGE = timedelta(days=7)
WATER_TEMP_MIN_C = 0.0
WATER_TEMP_MAX_C = 40.0


def _to_zurich(stamp: datetime) -> datetime:
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=ZURICH_TZ)
    return stamp.astimezone(ZURICH_TZ)


def fresh_water_temp(
    water_temp_c: float | None,
    *,
    observed_at: datetime | None,
    source_modified_at: datetime | None,
    now: datetime,
) -> float | None:
    """Return water temp if both freshness gates pass and value is sane, else None."""
    if water_temp_c is None:
        return None
    if observed_at is None or source_modified_at is None:
        return None

    try:
        value = float(water_temp_c)
    except (TypeError, ValueError):
        return None

    if value < WATER_TEMP_MIN_C or value > WATER_TEMP_MAX_C:
        return None

    now_z = _to_zurich(now)
    if (now_z - _to_zurich(observed_at)) > DEFAULT_OBSERVATION_MAX_AGE:
        return None
    if (now_z - _to_zurich(source_modified_at)) > WATER_TEMP_MAX_SOURCE_AGE:
        return None

    return value
