"""Opening-hours resolution — single source of truth for API and ML.

Supports both the legacy flat ``schedule`` shape in pool_metadata.json and the
generated periods/intervals/closures shape. Precedence inside ``resolve``:

    fresh observation → full closure → schedule interval → season window
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import holidays
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ZURICH_TZ = ZoneInfo("Europe/Zurich")
# Käferberg publishes up to 5 disjoint windows on some weekdays.
MAX_INTERVALS = 6
DEFAULT_OBSERVATION_MAX_AGE = dt.timedelta(minutes=60)
STALENESS_DAYS = 14

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_INDEX = {name: i for i, name in enumerate(DAY_NAMES)}
SCHEMA_DAY_URL = {
    0: "https://schema.org/Monday",
    1: "https://schema.org/Tuesday",
    2: "https://schema.org/Wednesday",
    3: "https://schema.org/Thursday",
    4: "https://schema.org/Friday",
    5: "https://schema.org/Saturday",
    6: "https://schema.org/Sunday",
}
DE_MONTHS = [
    "Jan",
    "Feb",
    "Mär",
    "Apr",
    "Mai",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dez",
]
DE_DAYS_SHORT = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]

Condition = Literal["always", "fair_weather"]
Confidence = Literal["official_structured", "official_prose", "unverified"]
GENERATED_PATH = Path(__file__).parent / "data" / "opening_hours.generated.json"
METADATA_PATH = Path(__file__).parent / "pool_metadata.json"

_HOLIDAYS_CACHE: dict[str, holidays.HolidayBase] = {}


def _get_holidays(country: str = "CH", subdiv: str = "ZH") -> holidays.HolidayBase:
    key = f"{country}_{subdiv}"
    if key not in _HOLIDAYS_CACHE:
        _HOLIDAYS_CACHE[key] = holidays.country_holidays(country, subdiv=subdiv)
    return _HOLIDAYS_CACHE[key]


class OpenState(str, Enum):
    OBSERVED_OPEN = "observed_open"
    OBSERVED_CLOSED = "observed_closed"
    OPEN_GUARANTEED = "open_guaranteed"
    OPEN_CONDITIONAL = "open_conditional"
    CLOSED_BETWEEN = "closed_between"
    CLOSED_TODAY = "closed_today"
    CLOSED_EXCEPTION = "closed_exception"
    OFF_SEASON = "off_season"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Interval:
    open_min: int
    close_min: int
    condition: Condition = "always"

    def __post_init__(self) -> None:
        if not (0 <= self.open_min < self.close_min <= 1440):
            raise ValueError(
                f"Invalid interval {self.open_min}-{self.close_min}: "
                "must satisfy 0 <= open < close <= 1440"
            )


@dataclass(frozen=True)
class Period:
    start: dt.date | None
    end: dt.date | None
    days: frozenset[int]
    intervals: tuple[Interval, ...]

    def covers(self, day: dt.date) -> bool:
        if self.start is not None and day < self.start:
            return False
        if self.end is not None and day > self.end:
            return False
        return True


@dataclass(frozen=True)
class Closure:
    start: dt.datetime
    end: dt.datetime
    reason: str
    scope: Literal["full", "partial"] = "full"
    cited_sentence: str | None = None
    extracted_by: str | None = None

    def active_at(self, when: dt.datetime) -> bool:
        return self.start <= when < self.end


@dataclass(frozen=True)
class PoolSchedule:
    uid: str
    periods: tuple[Period, ...]
    closures: tuple[Closure, ...] = ()
    holidays_follow: int | None = None  # weekday index whose hours holidays inherit
    last_entry_offset_min: int = 30
    confidence: Confidence = "unverified"
    scraped_at: dt.date | None = None


@dataclass(frozen=True)
class Observation:
    observed_at: dt.datetime
    source_modified_at: dt.datetime | None
    is_open: bool | None  # None when the feed field was empty


@dataclass(frozen=True)
class WeatherHint:
    temperature_c: float | None = None
    precipitation_mm: float | None = None
    weathercode: int | None = None


@dataclass(frozen=True)
class Resolution:
    state: OpenState
    is_open: bool
    guaranteed_close: dt.time | None = None
    conditional_close: dt.time | None = None
    reason: str | None = None
    next_open: str | None = None
    opens_seasonal: str | None = None
    source: Literal["observed", "closure", "schedule", "none"] = "schedule"
    confidence: Confidence = "unverified"


def _parse_hhmm(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _minutes_to_time(minutes: int) -> dt.time:
    if minutes >= 1440:
        return dt.time(23, 59)
    return dt.time(minutes // 60, minutes % 60)


def _to_zurich(when: dt.datetime) -> dt.datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=ZURICH_TZ)
    return when.astimezone(ZURICH_TZ)


def is_fair_weather(w: WeatherHint | None) -> bool | None:
    """Documented fair-weather predicate.

    Returns None when weather is unknown. Calibrated initially as:
    - precipitation_mm < 0.2
    - temperature_c is None or >= 16
    - weathercode is None or < 61 (no rain showers / thunderstorms)
    """
    if w is None:
        return None
    if w.precipitation_mm is None and w.temperature_c is None and w.weathercode is None:
        return None
    if w.precipitation_mm is not None and w.precipitation_mm >= 0.2:
        return False
    if w.temperature_c is not None and w.temperature_c < 16:
        return False
    if w.weathercode is not None and w.weathercode >= 61:
        return False
    return True


def _legacy_to_schedule(uid: str, opening_hours: dict) -> PoolSchedule:
    """Convert the flat schedule / seasonal_open shape into Periods."""
    schedule = opening_hours.get("schedule") or {}
    seasonal_open = opening_hours.get("seasonal_open")
    seasonal_close = opening_hours.get("seasonal_close")
    start = dt.date.fromisoformat(seasonal_open) if seasonal_open else None
    end = dt.date.fromisoformat(seasonal_close) if seasonal_close else None

    # Group days that share the same open/close into one period for compactness.
    by_hours: dict[tuple[str, str] | None, list[int]] = {}
    for day_name in DAY_NAMES:
        slot = schedule.get(day_name)
        if not slot:
            by_hours.setdefault(None, []).append(DAY_INDEX[day_name])
            continue
        key = (slot["open"], slot["close"])
        by_hours.setdefault(key, []).append(DAY_INDEX[day_name])

    periods: list[Period] = []
    for key, days in by_hours.items():
        if key is None:
            continue
        open_s, close_s = key
        periods.append(
            Period(
                start=start,
                end=end,
                days=frozenset(days),
                intervals=(
                    Interval(
                        open_min=_parse_hhmm(open_s),
                        close_min=_parse_hhmm(close_s),
                        condition="always",
                    ),
                ),
            )
        )

    # Year-round pools with no open days still need an empty periods list so
    # resolve() can report CLOSED_TODAY rather than UNKNOWN.
    confidence: Confidence = "unverified"
    return PoolSchedule(
        uid=uid,
        periods=tuple(periods),
        closures=(),
        holidays_follow=6,  # Sunday — matches Stadt Zürich tables
        confidence=confidence,
        scraped_at=None,
    )


def _parse_generated_schedule(uid: str, raw: dict) -> PoolSchedule:
    periods: list[Period] = []
    for p in raw.get("periods") or []:
        intervals = tuple(
            Interval(
                open_min=_parse_hhmm(i["open"]),
                close_min=_parse_hhmm(i["close"]),
                condition=i.get("condition", "always"),
            )
            for i in p.get("intervals") or []
        )
        if len(intervals) > MAX_INTERVALS:
            raise ValueError(
                f"{uid}: {len(intervals)} intervals exceeds MAX_INTERVALS={MAX_INTERVALS}"
            )
        days = frozenset(DAY_INDEX[d] for d in p.get("days") or DAY_NAMES)
        start = dt.date.fromisoformat(p["from"]) if p.get("from") else None
        end = dt.date.fromisoformat(p["to"]) if p.get("to") else None
        periods.append(Period(start=start, end=end, days=days, intervals=intervals))

    closures: list[Closure] = []
    for c in raw.get("closures") or []:
        start = dt.datetime.fromisoformat(c["from"])
        end = dt.datetime.fromisoformat(c["to"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZURICH_TZ)
        if end.tzinfo is None:
            end = end.replace(tzinfo=ZURICH_TZ)
        closures.append(
            Closure(
                start=start,
                end=end,
                reason=c.get("reason", "Geschlossen"),
                scope=c.get("scope", "full"),
                cited_sentence=c.get("cited_sentence"),
                extracted_by=c.get("extracted_by"),
            )
        )

    scraped_at = None
    if raw.get("scraped_at"):
        scraped_at = dt.date.fromisoformat(raw["scraped_at"])
    elif raw.get("source", {}).get("scraped_at"):
        scraped_at = dt.date.fromisoformat(raw["source"]["scraped_at"])

    holidays_follow = None
    hf = raw.get("holidays_follow")
    if hf in DAY_INDEX:
        holidays_follow = DAY_INDEX[hf]
    elif isinstance(hf, int):
        holidays_follow = hf

    confidence = raw.get("confidence") or raw.get("source", {}).get(
        "confidence", "official_structured"
    )
    return PoolSchedule(
        uid=uid,
        periods=tuple(periods),
        closures=tuple(closures),
        holidays_follow=holidays_follow,
        last_entry_offset_min=int(raw.get("last_entry_offset_min", 30)),
        confidence=confidence,
        scraped_at=scraped_at,
    )


def load_schedules(
    metadata_path: Path | None = None,
    generated_path: Path | None = None,
) -> dict[str, PoolSchedule]:
    """Load schedules for every pool, preferring the generated file when present."""
    meta_path = metadata_path or METADATA_PATH
    gen_path = generated_path or GENERATED_PATH

    pools = json.loads(meta_path.read_text(encoding="utf-8"))
    generated: dict[str, dict] = {}
    if gen_path.exists():
        raw = json.loads(gen_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "pools" in raw:
            generated = {p["uid"]: p for p in raw["pools"]}
        elif isinstance(raw, list):
            generated = {p["uid"]: p for p in raw}
        elif isinstance(raw, dict):
            generated = raw

    result: dict[str, PoolSchedule] = {}
    for pool in pools:
        uid = pool["uid"]
        if uid in generated and generated[uid].get("periods") is not None:
            try:
                schedule = _parse_generated_schedule(uid, generated[uid])
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Bad generated schedule for %s: %s; falling back", uid, exc
                )
                oh = pool.get("opening_hours") or {}
                schedule = _legacy_to_schedule(uid, oh)
        elif pool.get("opening_hours"):
            schedule = _legacy_to_schedule(uid, pool["opening_hours"])
            # Merge closures from generated file even when periods are absent
            if uid in generated and generated[uid].get("closures"):
                try:
                    gen = _parse_generated_schedule(uid, generated[uid])
                    schedule = PoolSchedule(
                        uid=schedule.uid,
                        periods=schedule.periods,
                        closures=gen.closures,
                        holidays_follow=schedule.holidays_follow,
                        last_entry_offset_min=gen.last_entry_offset_min
                        or schedule.last_entry_offset_min,
                        confidence=(
                            gen.confidence if gen.closures else schedule.confidence
                        ),
                        scraped_at=gen.scraped_at or schedule.scraped_at,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("Bad generated closures for %s: %s", uid, exc)
        else:
            # No hours at all — always-open for ML compatibility, soft UI elsewhere
            schedule = PoolSchedule(
                uid=uid,
                periods=(
                    Period(
                        start=None,
                        end=None,
                        days=frozenset(range(7)),
                        intervals=(Interval(0, 1440, "always"),),
                    ),
                ),
                confidence="unverified",
            )
        result[uid] = schedule
    return result


def _effective_weekday(schedule: PoolSchedule, day: dt.date) -> int:
    weekday = day.weekday()
    if schedule.holidays_follow is not None:
        if day in _get_holidays():
            return schedule.holidays_follow
    return weekday


def _periods_for_day(schedule: PoolSchedule, day: dt.date) -> list[Period]:
    weekday = _effective_weekday(schedule, day)
    return [
        p
        for p in schedule.periods
        if p.covers(day) and weekday in p.days and p.intervals
    ]


def _has_any_season(schedule: PoolSchedule) -> bool:
    return any(p.start is not None or p.end is not None for p in schedule.periods)


def _next_season_open(schedule: PoolSchedule, day: dt.date) -> dt.date | None:
    starts = sorted(
        p.start for p in schedule.periods if p.start is not None and p.start > day
    )
    if starts:
        return starts[0]
    # Wrap to next year's earliest start when all published starts are in the past.
    all_starts = sorted(p.start for p in schedule.periods if p.start is not None)
    if not all_starts:
        return None
    earliest = all_starts[0]
    return earliest.replace(year=day.year + 1)


def _active_full_closure(schedule: PoolSchedule, when: dt.datetime) -> Closure | None:
    for closure in schedule.closures:
        if closure.scope == "full" and closure.active_at(when):
            return closure
    return None


def _observation_is_fresh(
    observation: Observation | None,
    when: dt.datetime,
    max_age: dt.timedelta,
) -> bool:
    if observation is None or observation.is_open is None:
        return False
    stamp = observation.source_modified_at or observation.observed_at
    stamp = _to_zurich(stamp)
    return (when - stamp) <= max_age


def _format_next_open(schedule: PoolSchedule, when: dt.datetime) -> str | None:
    """Return a display string for the next opening time from the schedule."""
    day = when.date()
    current_min = when.hour * 60 + when.minute

    # Still today?
    for period in _periods_for_day(schedule, day):
        for interval in period.intervals:
            if interval.condition != "always":
                continue
            if current_min < interval.open_min:
                return _minutes_to_time(interval.open_min).strftime("%H:%M")

    for offset in range(1, 8):
        check_day = day + dt.timedelta(days=offset)
        for period in _periods_for_day(schedule, check_day):
            always = [i for i in period.intervals if i.condition == "always"]
            if not always:
                continue
            t = _minutes_to_time(always[0].open_min).strftime("%H:%M")
            if offset == 1:
                return t
            return f"{DE_DAYS_SHORT[check_day.weekday()]} {t}"
    return None


def _close_times_for_day(
    periods: list[Period],
) -> tuple[dt.time | None, dt.time | None]:
    guaranteed: int | None = None
    conditional: int | None = None
    for period in periods:
        for interval in period.intervals:
            if interval.condition == "always":
                guaranteed = (
                    interval.close_min
                    if guaranteed is None
                    else max(guaranteed, interval.close_min)
                )
            else:
                conditional = (
                    interval.close_min
                    if conditional is None
                    else max(conditional, interval.close_min)
                )
    return (
        _minutes_to_time(guaranteed) if guaranteed is not None else None,
        _minutes_to_time(conditional) if conditional is not None else None,
    )


def _effective_confidence(schedule: PoolSchedule, today: dt.date) -> Confidence:
    if schedule.confidence == "unverified":
        return "unverified"
    if schedule.scraped_at is None:
        return schedule.confidence
    age = (today - schedule.scraped_at).days
    if age > STALENESS_DAYS:
        return "unverified"
    return schedule.confidence


def resolve(
    schedule: PoolSchedule,
    when: dt.datetime,
    *,
    weather: WeatherHint | None = None,
    observation: Observation | None = None,
    observation_max_age: dt.timedelta = DEFAULT_OBSERVATION_MAX_AGE,
) -> Resolution:
    """Resolve open/closed state for one pool at one instant."""
    when = _to_zurich(when)
    day = when.date()
    current_min = when.hour * 60 + when.minute
    confidence = _effective_confidence(schedule, day)

    # 1. Fresh observation wins for is_open only
    if _observation_is_fresh(observation, when, observation_max_age):
        assert observation is not None and observation.is_open is not None
        if observation.is_open:
            g_close, c_close = _close_times_for_day(_periods_for_day(schedule, day))
            return Resolution(
                state=OpenState.OBSERVED_OPEN,
                is_open=True,
                guaranteed_close=g_close,
                conditional_close=c_close,
                source="observed",
                confidence=confidence,
            )
        return Resolution(
            state=OpenState.OBSERVED_CLOSED,
            is_open=False,
            reason="Aktuell geschlossen",
            next_open=_format_next_open(schedule, when),
            source="observed",
            confidence=confidence,
        )

    # 2. Full closure
    closure = _active_full_closure(schedule, when)
    if closure is not None:
        # Closure.end is exclusive; display the inclusive last closed day.
        inclusive_end = (closure.end - dt.timedelta(minutes=1)).date()
        end_label = f"{inclusive_end.day}. {DE_MONTHS[inclusive_end.month - 1]}"
        # next_open always comes from the schedule, starting at closure end.
        return Resolution(
            state=OpenState.CLOSED_EXCEPTION,
            is_open=False,
            reason=f"{closure.reason} bis {end_label}",
            next_open=_format_next_open(schedule, closure.end),
            source="closure",
            confidence=confidence,
        )

    # 3. Season / period
    periods_today = _periods_for_day(schedule, day)
    if not periods_today:
        # Distinguish off-season from closed-today
        if _has_any_season(schedule):
            any_period_covers = any(p.covers(day) for p in schedule.periods)
            if not any_period_covers:
                season_open = _next_season_open(schedule, day)
                label = None
                if season_open is not None:
                    label = f"ab {season_open.day}. {DE_MONTHS[season_open.month - 1]}"
                return Resolution(
                    state=OpenState.OFF_SEASON,
                    is_open=False,
                    opens_seasonal=label,
                    source="schedule",
                    confidence=confidence,
                )
        return Resolution(
            state=OpenState.CLOSED_TODAY,
            is_open=False,
            next_open=_format_next_open(schedule, when),
            source="schedule",
            confidence=confidence,
        )

    # 4. Interval membership
    fair = is_fair_weather(weather)
    matching: list[Interval] = []
    for period in periods_today:
        for interval in period.intervals:
            if interval.condition == "fair_weather" and fair is not True:
                continue
            if interval.open_min <= current_min < interval.close_min:
                matching.append(interval)

    g_close, c_close = _close_times_for_day(periods_today)

    if matching:
        state = (
            OpenState.OPEN_CONDITIONAL
            if any(i.condition == "fair_weather" for i in matching)
            else OpenState.OPEN_GUARANTEED
        )
        return Resolution(
            state=state,
            is_open=True,
            guaranteed_close=g_close,
            conditional_close=c_close,
            source="schedule",
            confidence=confidence,
        )

    return Resolution(
        state=OpenState.CLOSED_BETWEEN,
        is_open=False,
        guaranteed_close=g_close,
        conditional_close=c_close,
        next_open=_format_next_open(schedule, when),
        source="schedule",
        confidence=confidence,
    )


def resolution_to_api_dict(resolution: Resolution) -> dict:
    """Map a Resolution to the legacy API shape plus new fields."""
    return {
        "is_open": resolution.is_open,
        "next_open": resolution.next_open,
        "opens_seasonal": resolution.opens_seasonal,
        "state": resolution.state.value,
        "reason": resolution.reason,
        "confidence": resolution.confidence,
        "guaranteed_close": (
            resolution.guaranteed_close.strftime("%H:%M")
            if resolution.guaranteed_close
            else None
        ),
        "conditional_close": (
            resolution.conditional_close.strftime("%H:%M")
            if resolution.conditional_close
            else None
        ),
        "source": resolution.source,
    }


def use_observed_override() -> bool:
    return os.getenv("OPENING_HOURS_USE_OBSERVED", "1") not in ("0", "false", "False")


def observation_from_status_text(
    status_text: str | None,
    *,
    observed_at: dt.datetime,
    source_modified_at: dt.datetime | None,
) -> Observation:
    is_open: bool | None
    if not status_text:
        is_open = None
    elif status_text.strip().lower() in {"offen", "open"}:
        is_open = True
    elif status_text.strip().lower() in {"geschlossen", "closed"}:
        is_open = False
    else:
        is_open = None
    return Observation(
        observed_at=observed_at,
        source_modified_at=source_modified_at,
        is_open=is_open,
    )


def resolve_frame(
    df: pd.DataFrame,
    schedules: dict[str, PoolSchedule],
    weather: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Vectorized opening-hours features for training/inference.

    Adds ``is_open``, ``minutes_since_open``, ``minutes_until_close``.
    Requires ``pool_uid``, ``hour_of_day``, and either ``date`` or ``time``.
    Honours closures and ``always`` intervals; fair-weather intervals are
    treated as open when weather is unknown (conservative for training —
    occupancy during those hours is real signal) and closed when weather says
    unfair.
    """
    out = df.copy()
    if out.empty:
        out["is_open"] = pd.Series(dtype=int)
        out["minutes_since_open"] = pd.Series(dtype=int)
        out["minutes_until_close"] = pd.Series(dtype=int)
        return out

    if "date" in out.columns:
        local_dates = pd.to_datetime(out["date"]).dt.date
    else:
        dt_series = pd.to_datetime(out["time"])
        if getattr(dt_series.dt, "tz", None) is not None:
            dt_series = dt_series.dt.tz_convert("Europe/Zurich")
        local_dates = dt_series.dt.date

    out["_date"] = local_dates
    out["_dow"] = pd.to_datetime(out["_date"].astype(str)).dt.weekday

    # Build (pool_uid, date) lookup with up to MAX_INTERVALS intervals
    keys = out[["pool_uid", "_date", "_dow"]].drop_duplicates()
    rows: list[dict] = []
    for pool_uid, day, dow in keys.itertuples(index=False, name=None):
        schedule = schedules.get(pool_uid)
        row: dict = {
            "pool_uid": pool_uid,
            "_date": day,
            "full_closure": 0,
            **{f"i{i}_open": -1 for i in range(1, MAX_INTERVALS + 1)},
            **{f"i{i}_close": -1 for i in range(1, MAX_INTERVALS + 1)},
            **{f"i{i}_cond": 0 for i in range(1, MAX_INTERVALS + 1)},
        }
        if schedule is None:
            # Always open — matches historical defensive behaviour
            row["i1_open"] = 0
            row["i1_close"] = 1440
            rows.append(row)
            continue

        # Closures: mark whole day closed if any full closure overlaps the day
        day_start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=ZURICH_TZ)
        day_end = day_start + dt.timedelta(days=1)
        for closure in schedule.closures:
            if closure.scope != "full":
                continue
            if closure.start < day_end and closure.end > day_start:
                # If closure covers the whole day, mark closed; partial-day
                # closures are approximated as closed for ML simplicity in v1.
                row["full_closure"] = 1
                break

        periods = _periods_for_day(schedule, day) if not row["full_closure"] else []
        intervals: list[Interval] = []
        for period in periods:
            intervals.extend(period.intervals)
        intervals = sorted(intervals, key=lambda i: i.open_min)[:MAX_INTERVALS]
        for idx, interval in enumerate(intervals, start=1):
            row[f"i{idx}_open"] = interval.open_min
            row[f"i{idx}_close"] = interval.close_min
            row[f"i{idx}_cond"] = 1 if interval.condition == "fair_weather" else 0
        rows.append(row)

    lut = pd.DataFrame(rows)
    out = out.merge(lut, on=["pool_uid", "_date"], how="left")
    out["full_closure"] = out["full_closure"].fillna(0).astype(int)

    current_min = out["hour_of_day"].astype(int) * 60

    # Fair-weather intervals: open when weather is unknown (conservative for
    # training — occupancy during those hours is real signal). When weather is
    # known, apply the same predicate as is_fair_weather().
    #
    # "Known" means at least one of precip / weathercode / is_rainy is present
    # and not NaN. Default-filled 15°C / 0mm without weathercode must NOT count
    # as known — that was collapsing afternoon Freibäder to closed in training.
    fair_series = pd.Series(True, index=out.index)
    has_precip = "precipitation_mm" in out.columns
    has_code = "weathercode" in out.columns
    has_rainy = "is_rainy" in out.columns
    if has_precip or has_code or has_rainy:
        known = pd.Series(False, index=out.index)
        precip = (
            out["precipitation_mm"]
            if has_precip
            else pd.Series(np.nan, index=out.index)
        )
        temp = (
            out["temperature_c"]
            if "temperature_c" in out.columns
            else pd.Series(np.nan, index=out.index)
        )
        code = out["weathercode"] if has_code else pd.Series(np.nan, index=out.index)
        rainy = out["is_rainy"] if has_rainy else pd.Series(np.nan, index=out.index)

        known = known | precip.notna() | code.notna() | rainy.notna() | temp.notna()
        # Match is_fair_weather: precip < 0.2, temp >= 16 or null, code < 61 or null
        computed = (precip.isna() | (precip < 0.2)) & (temp.isna() | (temp >= 16))
        if has_code:
            computed = computed & (code.isna() | (code < 61))
        elif has_rainy:
            # is_rainy uses weathercode >= 51; treat rainy as unfair
            computed = computed & (rainy.isna() | (rainy == 0))
        # Unknown rows stay True (open); known rows use the predicate
        fair_series = (~known) | computed

    open_mask = pd.Series(False, index=out.index)
    since = pd.Series(0, index=out.index, dtype=int)
    until = pd.Series(0, index=out.index, dtype=int)

    for idx in range(1, MAX_INTERVALS + 1):
        o = out[f"i{idx}_open"].fillna(-1).astype(int)
        c = out[f"i{idx}_close"].fillna(-1).astype(int)
        cond = out[f"i{idx}_cond"].fillna(0).astype(int)
        active = (o >= 0) & (current_min >= o) & (current_min < c)
        active = active & ((cond == 0) | fair_series)
        open_mask = open_mask | active
        since = np.where(active & (since == 0), current_min - o, since)
        until = np.where(active & (until == 0), c - current_min, until)

    open_mask = open_mask & (out["full_closure"] == 0)
    out["is_open"] = open_mask.astype(int)
    out["minutes_since_open"] = np.where(open_mask, since, 0).astype(int)
    out["minutes_until_close"] = np.where(open_mask, until, 0).astype(int)

    drop_cols = ["_date", "_dow", "full_closure"]
    for idx in range(1, MAX_INTERVALS + 1):
        drop_cols.extend([f"i{idx}_open", f"i{idx}_close", f"i{idx}_cond"])
    out = out.drop(columns=drop_cols, errors="ignore")
    return out


def count_open_hours(schedule: PoolSchedule, day: dt.date) -> int:
    """Count hours that have an always-condition interval covering the hour start."""
    if _active_full_closure(
        schedule, dt.datetime.combine(day, dt.time(12, 0), tzinfo=ZURICH_TZ)
    ):
        return 0
    periods = _periods_for_day(schedule, day)
    if not periods:
        return 0
    count = 0
    for hour in range(24):
        minute = hour * 60
        for period in periods:
            for interval in period.intervals:
                if interval.condition != "always":
                    continue
                if interval.open_min <= minute < interval.close_min:
                    count += 1
                    break
            else:
                continue
            break
    return count


def is_off_season(schedule: PoolSchedule, day: dt.date) -> bool:
    if not _has_any_season(schedule):
        return False
    return not any(p.covers(day) for p in schedule.periods)


def _minutes_to_hhmmss(minutes: int) -> str:
    """Format minutes-from-midnight as schema.org HH:MM:SS."""
    if minutes >= 1440:
        return "23:59:00"
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def _fmt_clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def opening_hours_jsonld(schedule: PoolSchedule) -> list[dict]:
    """Build Hours JSON-LD (Guaranteed hours + full Closures only).

    See docs/adr/ADR-001-guaranteed-hours-in-structured-data.md. Conditional
    (fair_weather) intervals are never emitted as opens/closes.
    """
    specs: list[dict] = []
    for period in schedule.periods:
        dated = period.start is not None and period.end is not None
        for day_idx in sorted(period.days):
            for interval in period.intervals:
                if interval.condition != "always":
                    continue
                spec: dict = {
                    "@type": "OpeningHoursSpecification",
                    "dayOfWeek": SCHEMA_DAY_URL[day_idx],
                    "opens": _minutes_to_hhmmss(interval.open_min),
                    "closes": _minutes_to_hhmmss(interval.close_min),
                }
                if dated:
                    spec["validFrom"] = period.start.isoformat()  # type: ignore[union-attr]
                    spec["validThrough"] = period.end.isoformat()  # type: ignore[union-attr]
                specs.append(spec)

    for closure in schedule.closures:
        if closure.scope != "full":
            continue
        start = _to_zurich(closure.start)
        end = _to_zurich(closure.end)
        inclusive_end = (end - dt.timedelta(minutes=1)).date()
        specs.append(
            {
                "@type": "OpeningHoursSpecification",
                "opens": "00:00:00",
                "closes": "00:00:00",
                "validFrom": start.date().isoformat(),
                "validThrough": inclusive_end.isoformat(),
            }
        )
    return specs


def _fmt_closure_end_label(closure: Closure) -> str:
    inclusive = (_to_zurich(closure.end) - dt.timedelta(minutes=1)).date()
    return f"{inclusive.day}. {DE_MONTHS[inclusive.month - 1]}"


def _active_full_closures(schedule: PoolSchedule, when: dt.datetime) -> list[Closure]:
    when = _to_zurich(when)
    return [c for c in schedule.closures if c.scope == "full" and c.active_at(when)]


def _next_full_closure(schedule: PoolSchedule, when: dt.datetime) -> Closure | None:
    when = _to_zurich(when)
    upcoming = [
        c for c in schedule.closures if c.scope == "full" and _to_zurich(c.start) > when
    ]
    if not upcoming:
        return None
    return min(upcoming, key=lambda c: c.start)


def _faq_upcoming_closure_suffix(schedule: PoolSchedule, when: dt.datetime) -> str:
    """Mention the next upcoming full Closure (not currently active)."""
    closure = _next_full_closure(schedule, when)
    if closure is None:
        return ""
    start_d = _to_zurich(closure.start).date()
    return (
        f" Hinweis: {closure.reason} vom "
        f"{start_d.day}. {DE_MONTHS[start_d.month - 1]} bis "
        f"{_fmt_closure_end_label(closure)}."
    )


def opening_hours_faq_text(
    schedule: PoolSchedule,
    pool_name: str,
    *,
    when: dt.datetime | None = None,
) -> str:
    """German FAQ answer matching Guaranteed hours + Conditional hours prose.

    Uses the SEO “sicher geöffnet” pattern only when every open weekday shares
    the same single always window. Weekday-varying Hallenbäder point at the
    page instead of inventing a false “täglich” soup. An active full Closure
    leads the answer so we never claim open during Revision.
    """
    fair_closes: set[str] = set()
    season_start: dt.date | None = None
    season_end: dt.date | None = None
    # day_idx → set of always (open_min, close_min) windows that day
    day_always: dict[int, set[tuple[int, int]]] = {i: set() for i in range(7)}

    for period in schedule.periods:
        if period.start is not None and (
            season_start is None or period.start < season_start
        ):
            season_start = period.start
        if period.end is not None and (season_end is None or period.end > season_end):
            season_end = period.end
        always_here = [
            (i.open_min, i.close_min)
            for i in period.intervals
            if i.condition == "always"
        ]
        for interval in period.intervals:
            if interval.condition == "fair_weather":
                fair_closes.add(_fmt_clock(interval.close_min))
        if not always_here:
            continue
        for day_idx in period.days:
            day_always[day_idx].update(always_here)

    active_days = {d: frozenset(wins) for d, wins in day_always.items() if wins}
    has_fair = bool(fair_closes)
    now = when if when is not None else dt.datetime.now(ZURICH_TZ)

    active = _active_full_closures(schedule, now)
    if active:
        closure = min(active, key=lambda c: c.end)
        return (
            f"{pool_name} ist derzeit geschlossen ({closure.reason} bis "
            f"{_fmt_closure_end_label(closure)}). "
            f"Übliche Öffnungszeiten finden Sie auf dieser Seite."
        )

    upcoming_note = _faq_upcoming_closure_suffix(schedule, now)

    if not active_days and not has_fair:
        base = (
            f"Die aktuellen Öffnungszeiten von {pool_name} finden Sie auf dieser Seite."
        )
        return base + upcoming_note
    if not active_days:
        base = (
            f"{pool_name} hat wetterabhängige Öffnungszeiten; "
            f"aktuelle Hinweise finden Sie auf dieser Seite."
        )
        return base + upcoming_note

    patterns = set(active_days.values())
    uniform_single = len(patterns) == 1 and len(next(iter(patterns))) == 1

    def _fair_suffix() -> str:
        if not fair_closes:
            return ""
        closes = sorted(fair_closes)
        if len(closes) == 1:
            fair_phrase = f"bis {closes[0]} Uhr"
        else:
            fair_phrase = "bis " + " oder ".join(closes) + " Uhr"
        return (
            f" Bei schönem Wetter kann es je nach Saisonabschnitt {fair_phrase} "
            f"geöffnet bleiben; aktuelle Hinweise und Schliesszeiten finden Sie "
            f"auf dieser Seite."
        )

    if not uniform_single:
        text = (
            f"Die Öffnungszeiten von {pool_name} variieren nach Wochentag; "
            f"aktuelle Zeiten finden Sie auf dieser Seite."
        )
        return text + _fair_suffix() + upcoming_note

    open_m, close_m = next(iter(next(iter(patterns))))
    guaranteed = f"von {_fmt_clock(open_m)} bis {_fmt_clock(close_m)} Uhr"
    day_word = "täglich" if set(active_days) == set(range(7)) else "an Öffnungstagen"
    text = (
        f"{pool_name} ist während der Saison {day_word} {guaranteed} sicher geöffnet."
    )
    fair = _fair_suffix()
    if fair:
        return text + fair + upcoming_note
    if season_start is not None and season_end is not None:
        text += (
            f" Saison: {season_start.day}. {DE_MONTHS[season_start.month - 1]} "
            f"bis {season_end.day}. {DE_MONTHS[season_end.month - 1]}."
        )
    else:
        text += " Aktuelle Hinweise finden Sie auf dieser Seite."
    return text + upcoming_note
