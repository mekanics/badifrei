"""Schedule helpers and prediction-day classification for /predict* routes."""

from dateutil.parser import parse as date_parser_raw


def date_parser(date_str: str):
    return date_parser_raw(date_str).date()


def _schedules():
    """Lazy-load PoolSchedule map (module-level cache)."""
    from ml.opening_hours import load_schedules

    if not hasattr(_schedules, "_cache") or _schedules._cache is None:
        _schedules._cache = load_schedules()
    return _schedules._cache


_schedules._cache = None  # type: ignore[attr-defined]


def _schedule_for_pool(pool: dict):
    """Return the PoolSchedule for a pool, building a one-off from legacy hours."""
    from ml.opening_hours import _legacy_to_schedule

    uid = pool.get("uid")
    schedules = _schedules()
    if uid in schedules:
        return schedules[uid]
    oh = pool.get("opening_hours")
    if oh:
        return _legacy_to_schedule(uid or "unknown", oh)
    return None


def _is_off_season(opening_hours: dict | None, day) -> bool:
    """Legacy helper kept for call sites; prefers the deep module when possible."""
    from ml.opening_hours import is_off_season, _legacy_to_schedule

    if not opening_hours:
        return False
    schedule = _legacy_to_schedule("tmp", opening_hours)
    return is_off_season(schedule, day)


def _schedule_for_day(opening_hours: dict | None, day):
    if not opening_hours:
        return {"open": "00:00", "close": "24:00"}
    from ml.features import _DAY_NAMES

    day_name = _DAY_NAMES[day.weekday()]
    return opening_hours.get("schedule", {}).get(day_name)


def _count_open_hours(day_schedule: dict | None) -> int:
    if not day_schedule:
        return 0
    try:
        open_h, open_m = map(int, day_schedule["open"].split(":"))
        close_h, close_m = map(int, day_schedule["close"].split(":"))
    except (KeyError, ValueError):
        return 24
    open_min = open_h * 60 + open_m
    close_min = close_h * 60 + close_m
    return sum(1 for hour in range(24) if open_min <= hour * 60 < close_min)


def _classify_prediction_day(
    pool: dict,
    day,
    model_available: bool,
) -> dict:
    """Classify prediction availability for a pool on a Zurich-local date."""
    from ml.opening_hours import count_open_hours, is_off_season

    schedule = _schedule_for_pool(pool)
    if schedule is not None:
        if is_off_season(schedule, day):
            prediction_status = "off_season"
            open_hours_count = 0
        else:
            open_hours_count = count_open_hours(schedule, day)
            if open_hours_count == 0:
                prediction_status = "closed_all_day"
            elif not model_available:
                prediction_status = "no_model"
            else:
                prediction_status = "ok"
    else:
        opening_hours = pool.get("opening_hours")
        if _is_off_season(opening_hours, day):
            prediction_status = "off_season"
            open_hours_count = 0
        else:
            open_hours_count = _count_open_hours(_schedule_for_day(opening_hours, day))
            if open_hours_count == 0:
                prediction_status = "closed_all_day"
            elif not model_available:
                prediction_status = "no_model"
            else:
                prediction_status = "ok"

    return {
        "model_available": model_available,
        "prediction_status": prediction_status,
        "open_hours_count": open_hours_count,
    }
