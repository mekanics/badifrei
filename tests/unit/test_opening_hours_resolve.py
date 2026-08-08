"""Tests for ml.opening_hours.resolve — closures, observations, periods."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from ml.opening_hours import (
    Closure,
    Interval,
    Observation,
    OpenState,
    Period,
    PoolSchedule,
    WeatherHint,
    _legacy_to_schedule,
    _next_season_open,
    is_fair_weather,
    load_schedules,
    resolve,
    resolve_frame,
)

ZURICH = ZoneInfo("Europe/Zurich")


def _when(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZURICH)


def _simple_schedule(
    open_s="09:00",
    close_s="20:00",
    seasonal_open=None,
    seasonal_close=None,
    closures=(),
) -> PoolSchedule:
    oh = {
        "schedule": {
            day: {"open": open_s, "close": close_s}
            for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        },
        "seasonal_open": seasonal_open,
        "seasonal_close": seasonal_close,
    }
    base = _legacy_to_schedule("test", oh)
    return PoolSchedule(
        uid=base.uid,
        periods=base.periods,
        closures=tuple(closures),
        holidays_follow=base.holidays_follow,
        confidence="official_structured",
        scraped_at=date(2026, 8, 4),
    )


class TestResolveBasics:
    def test_open_at_half_past(self):
        schedule = _simple_schedule("06:30", "20:00")
        result = resolve(schedule, _when(2026, 3, 20, 6, 34))
        assert result.is_open is True
        assert result.state == OpenState.OPEN_GUARANTEED

    def test_closed_before_open(self):
        schedule = _simple_schedule("09:00", "20:00")
        result = resolve(schedule, _when(2026, 3, 20, 8, 59))
        assert result.is_open is False
        assert result.next_open == "09:00"

    def test_off_season(self):
        schedule = _simple_schedule(
            seasonal_open="2026-05-01", seasonal_close="2026-09-30"
        )
        result = resolve(schedule, _when(2026, 3, 20, 14))
        assert result.is_open is False
        assert result.state == OpenState.OFF_SEASON
        assert result.opens_seasonal is not None
        assert "Mai" in result.opens_seasonal


class TestClosures:
    def test_full_closure_closes_pool(self):
        closure = Closure(
            start=_when(2026, 7, 4, 0),
            end=_when(2026, 8, 8, 0),
            reason="Revision",
            scope="full",
        )
        schedule = _simple_schedule(closures=(closure,))
        result = resolve(schedule, _when(2026, 8, 4, 12))
        assert result.is_open is False
        assert result.state == OpenState.CLOSED_EXCEPTION
        assert result.reason is not None
        assert "Revision" in result.reason

    def test_partial_closure_keeps_pool_open(self):
        closure = Closure(
            start=_when(2026, 8, 4, 0),
            end=_when(2026, 8, 5, 0),
            reason="Sprungbecken",
            scope="partial",
        )
        schedule = _simple_schedule(closures=(closure,))
        result = resolve(schedule, _when(2026, 8, 4, 12))
        assert result.is_open is True

    def test_generated_revision_closes_city(self):
        schedules = load_schedules()
        city = schedules["SSD-4"]
        result = resolve(city, _when(2026, 8, 4, 12))
        assert result.is_open is False
        assert result.state == OpenState.CLOSED_EXCEPTION


class TestObservations:
    def test_fresh_observation_overrides_open(self):
        schedule = _simple_schedule()
        obs = Observation(
            observed_at=_when(2026, 8, 4, 12),
            source_modified_at=_when(2026, 8, 4, 11, 55),
            is_open=False,
        )
        result = resolve(schedule, _when(2026, 8, 4, 12), observation=obs)
        assert result.is_open is False
        assert result.state == OpenState.OBSERVED_CLOSED

    def test_fresh_poll_keeps_closed_when_date_modified_aged(self):
        """Baditicker dateModified often sticks at last status change.

        Collector writes a new observed_at every poll; freshness must key off
        that confirmation so an afternoon weather-close stays observed-closed
        after source_modified_at ages past 60 minutes.
        """
        schedule = _simple_schedule()
        obs = Observation(
            observed_at=_when(2026, 8, 4, 16, 5),
            source_modified_at=_when(2026, 8, 4, 15, 0),
            is_open=False,
        )
        result = resolve(schedule, _when(2026, 8, 4, 16, 5), observation=obs)
        assert result.is_open is False
        assert result.state == OpenState.OBSERVED_CLOSED
        assert result.source == "observed"

    def test_stale_observation_ignored(self):
        schedule = _simple_schedule()
        obs = Observation(
            observed_at=_when(2026, 2, 8, 10, 42),
            source_modified_at=_when(2026, 2, 8, 10, 42),
            is_open=False,
        )
        result = resolve(schedule, _when(2026, 8, 4, 12), observation=obs)
        assert result.is_open is True
        assert result.source == "schedule"

    def test_empty_status_is_not_closed(self):
        schedule = _simple_schedule()
        obs = Observation(
            observed_at=_when(2026, 8, 4, 12),
            source_modified_at=_when(2026, 8, 4, 12),
            is_open=None,
        )
        result = resolve(schedule, _when(2026, 8, 4, 12), observation=obs)
        assert result.is_open is True
        assert result.source == "schedule"


class TestSplitIntervals:
    def test_gap_is_closed(self):
        schedule = PoolSchedule(
            uid="bungertwies",
            periods=(
                Period(
                    start=None,
                    end=None,
                    days=frozenset({0}),  # Monday
                    intervals=(
                        Interval(12 * 60, 13 * 60 + 30),
                        Interval(16 * 60, 19 * 60),
                    ),
                ),
            ),
            confidence="official_structured",
            scraped_at=date(2026, 8, 4),
        )
        # 2026-08-03 is a Monday
        assert resolve(schedule, _when(2026, 8, 3, 12, 30)).is_open is True
        assert resolve(schedule, _when(2026, 8, 3, 14, 30)).is_open is False
        assert resolve(schedule, _when(2026, 8, 3, 17, 0)).is_open is True


def _fair_weather_schedule(uid: str = "allenmoos") -> PoolSchedule:
    return PoolSchedule(
        uid=uid,
        periods=(
            Period(
                start=date(2026, 5, 30),
                end=date(2026, 8, 16),
                days=frozenset(range(7)),
                intervals=(
                    Interval(9 * 60, 14 * 60, "always"),
                    Interval(14 * 60, 21 * 60, "fair_weather"),
                ),
            ),
        ),
        confidence="official_structured",
        scraped_at=date(2026, 8, 4),
    )


class TestConditionalIntervals:
    def test_fair_weather_interval(self):
        schedule = _fair_weather_schedule()
        rainy = WeatherHint(temperature_c=20, precipitation_mm=2.0, weathercode=61)
        fair = WeatherHint(temperature_c=24, precipitation_mm=0.0, weathercode=1)
        assert resolve(schedule, _when(2026, 8, 4, 15), weather=rainy).is_open is False
        assert resolve(schedule, _when(2026, 8, 4, 15), weather=fair).is_open is True
        assert resolve(schedule, _when(2026, 8, 4, 15), weather=fair).state == (
            OpenState.OPEN_CONDITIONAL
        )

    def test_afternoon_without_weather_skips_fair_weather(self):
        """API path: unknown weather must not treat fair_weather as open."""
        schedule = _fair_weather_schedule()
        result = resolve(schedule, _when(2026, 8, 4, 15), weather=None)
        assert result.is_open is False
        assert result.state == OpenState.CLOSED_BETWEEN

    def test_exposes_both_close_times(self):
        schedule = PoolSchedule(
            uid="allenmoos",
            periods=(
                Period(
                    start=date(2026, 5, 30),
                    end=date(2026, 8, 16),
                    days=frozenset(range(7)),
                    intervals=(
                        Interval(9 * 60, 14 * 60, "always"),
                        Interval(14 * 60, 21 * 60, "fair_weather"),
                    ),
                ),
            ),
            confidence="official_structured",
            scraped_at=date(2026, 8, 4),
        )
        result = resolve(schedule, _when(2026, 8, 4, 10))
        assert result.guaranteed_close.strftime("%H:%M") == "14:00"
        assert result.conditional_close.strftime("%H:%M") == "21:00"


class TestFairWeather:
    def test_rain_is_unfair(self):
        assert is_fair_weather(WeatherHint(precipitation_mm=1.0)) is False

    def test_dry_warm_is_fair(self):
        assert (
            is_fair_weather(
                WeatherHint(temperature_c=22, precipitation_mm=0.0, weathercode=1)
            )
            is True
        )

    def test_thunderstorm_code_unfair_even_if_precip_zero(self):
        assert (
            is_fair_weather(
                WeatherHint(temperature_c=24, precipitation_mm=0.0, weathercode=95)
            )
            is False
        )

    def test_unknown_is_none(self):
        assert is_fair_weather(None) is None
        assert is_fair_weather(WeatherHint()) is None


class TestSeasonWrap:
    def test_next_season_open_wraps_year(self):
        schedule = _simple_schedule(
            seasonal_open="2026-05-09", seasonal_close="2026-09-20"
        )
        next_start = _next_season_open(schedule, date(2026, 11, 1))
        assert next_start == date(2027, 5, 9)

    def test_fb006_off_season_badge_points_at_next_year(self):
        schedules = load_schedules()
        result = resolve(schedules["fb006"], _when(2026, 11, 1, 12))
        assert result.state == OpenState.OFF_SEASON
        assert result.opens_seasonal == "ab 9. Mai"


class TestClosureNextOpen:
    def test_full_closure_sets_schedule_next_open(self):
        closure = Closure(
            start=_when(2026, 7, 4, 0),
            end=_when(2026, 8, 8, 0),
            reason="Revision",
            scope="full",
        )
        schedule = _simple_schedule("09:00", "20:00", closures=(closure,))
        result = resolve(schedule, _when(2026, 8, 4, 12))
        assert result.state == OpenState.CLOSED_EXCEPTION
        assert result.next_open is not None
        assert "09:00" in result.next_open


class TestResolveFrameFairWeather:
    def test_weathercode_thunderstorm_closes_afternoon(self):
        schedule = _fair_weather_schedule("fb-test")
        df = pd.DataFrame(
            {
                "pool_uid": ["fb-test"],
                "date": [date(2026, 8, 4)],
                "hour_of_day": [15],
                "temperature_c": [24.0],
                "precipitation_mm": [0.0],
                "weathercode": [95],
            }
        )
        out = resolve_frame(df, {"fb-test": schedule})
        assert out.iloc[0]["is_open"] == 0

    def test_fair_weather_opens_afternoon(self):
        schedule = _fair_weather_schedule("fb-test")
        df = pd.DataFrame(
            {
                "pool_uid": ["fb-test"],
                "date": [date(2026, 8, 4)],
                "hour_of_day": [15],
                "temperature_c": [24.0],
                "precipitation_mm": [0.0],
                "weathercode": [1],
            }
        )
        out = resolve_frame(df, {"fb-test": schedule})
        assert out.iloc[0]["is_open"] == 1

    def test_missing_weather_keeps_fair_weather_open(self):
        """Training path: unknown weather → treat fair_weather as open."""
        schedule = _fair_weather_schedule("fb-test")
        df = pd.DataFrame(
            {
                "pool_uid": ["fb-test"],
                "date": [date(2026, 8, 4)],
                "hour_of_day": [15],
            }
        )
        out = resolve_frame(df, {"fb-test": schedule})
        assert out.iloc[0]["is_open"] == 1

    def test_agrees_with_resolve_on_known_weather(self):
        schedule = _fair_weather_schedule("fb-test")
        fair = WeatherHint(temperature_c=24, precipitation_mm=0.0, weathercode=1)
        storm = WeatherHint(temperature_c=24, precipitation_mm=0.0, weathercode=95)
        when = _when(2026, 8, 4, 15)
        for hint in (fair, storm):
            df = pd.DataFrame(
                {
                    "pool_uid": ["fb-test"],
                    "date": [when.date()],
                    "hour_of_day": [when.hour],
                    "temperature_c": [hint.temperature_c],
                    "precipitation_mm": [hint.precipitation_mm],
                    "weathercode": [hint.weathercode],
                }
            )
            frame_open = int(
                resolve_frame(df, {"fb-test": schedule}).iloc[0]["is_open"]
            )
            scalar_open = int(resolve(schedule, when, weather=hint).is_open)
            assert frame_open == scalar_open
