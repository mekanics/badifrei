"""Hours JSON-LD and FAQ text — Guaranteed hours only (ADR-001)."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ml.opening_hours import (
    Closure,
    Interval,
    Period,
    PoolSchedule,
    _legacy_to_schedule,
    load_schedules,
    opening_hours_faq_text,
    opening_hours_jsonld,
)

ZURICH = ZoneInfo("Europe/Zurich")


def _when(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZURICH)


def _fair_weather_schedule() -> PoolSchedule:
    return PoolSchedule(
        uid="fb-test",
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
            Period(
                start=date(2026, 8, 17),
                end=date(2026, 9, 6),
                days=frozenset(range(7)),
                intervals=(
                    Interval(9 * 60, 14 * 60, "always"),
                    Interval(14 * 60, 20 * 60, "fair_weather"),
                ),
            ),
        ),
        confidence="official_structured",
        scraped_at=date(2026, 8, 4),
    )


class TestOpeningHoursJsonld:
    def test_allenmoos_guaranteed_only_no_fair_close(self):
        schedules = load_schedules()
        specs = opening_hours_jsonld(schedules["fb006"])
        assert specs
        assert json.dumps(specs)  # serializable

        mid = [
            s
            for s in specs
            if s.get("validFrom") == "2026-05-30"
            and s.get("validThrough") == "2026-08-16"
            and "dayOfWeek" in s
        ]
        assert mid
        assert all(s["opens"] == "09:00:00" for s in mid)
        assert all(s["closes"] == "14:00:00" for s in mid)
        assert all(s["closes"] != "21:00:00" for s in specs if "dayOfWeek" in s)

    def test_fair_weather_interval_never_in_opens_closes(self):
        specs = opening_hours_jsonld(_fair_weather_schedule())
        open_specs = [s for s in specs if "dayOfWeek" in s]
        assert open_specs
        assert {s["closes"] for s in open_specs} == {"14:00:00"}

    def test_times_are_hhmmss(self):
        specs = opening_hours_jsonld(_fair_weather_schedule())
        for s in specs:
            assert s["opens"].count(":") == 2
            assert s["closes"].count(":") == 2

    def test_full_closure_emits_midnight_pair(self):
        closure = Closure(
            start=_when(2026, 7, 4),
            end=_when(2026, 8, 8),
            reason="Revision",
            scope="full",
        )
        schedule = PoolSchedule(
            uid="closed",
            periods=(
                Period(
                    start=date(2026, 5, 1),
                    end=date(2026, 9, 30),
                    days=frozenset(range(7)),
                    intervals=(Interval(9 * 60, 20 * 60, "always"),),
                ),
            ),
            closures=(closure,),
            confidence="official_structured",
        )
        specs = opening_hours_jsonld(schedule)
        closed = [
            s for s in specs if s["opens"] == "00:00:00" and s["closes"] == "00:00:00"
        ]
        assert len(closed) == 1
        assert closed[0]["validFrom"] == "2026-07-04"
        assert closed[0]["validThrough"] == "2026-08-07"
        assert "dayOfWeek" not in closed[0]

    def test_partial_closure_omitted(self):
        closure = Closure(
            start=_when(2026, 8, 4),
            end=_when(2026, 8, 5),
            reason="Sprungbecken",
            scope="partial",
        )
        schedule = PoolSchedule(
            uid="partial",
            periods=(
                Period(
                    start=None,
                    end=None,
                    days=frozenset({0}),
                    intervals=(Interval(9 * 60, 20 * 60, "always"),),
                ),
            ),
            closures=(closure,),
        )
        specs = opening_hours_jsonld(schedule)
        assert not any(s["opens"] == "00:00:00" for s in specs)

    def test_letzigraben_always_pool(self):
        schedules = load_schedules()
        specs = opening_hours_jsonld(schedules["LETZI-1"])
        open_specs = [s for s in specs if "dayOfWeek" in s]
        assert open_specs
        assert all(s["opens"].endswith(":00") for s in open_specs)
        # Summer peak period includes 07:00–21:00 always
        peak = [
            s
            for s in open_specs
            if s.get("validFrom") == "2026-05-30"
            and s.get("validThrough") == "2026-08-16"
        ]
        assert peak
        assert any(s["closes"] == "21:00:00" for s in peak)

    def test_legacy_flat_no_crash(self):
        schedule = _legacy_to_schedule(
            "legacy",
            {
                "schedule": {
                    d: {"open": "09:00", "close": "20:00"}
                    for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                },
                "seasonal_open": "2026-05-01",
                "seasonal_close": "2026-09-30",
            },
        )
        specs = opening_hours_jsonld(schedule)
        assert len(specs) == 7
        assert all(s["opens"] == "09:00:00" for s in specs)
        assert all(s.get("validFrom") == "2026-05-01" for s in specs)
        assert all(s.get("validThrough") == "2026-09-30" for s in specs)

    def test_json_serializable(self):
        schedules = load_schedules()
        for uid in ("fb006", "LETZI-1", "SSD-4", "IMBAD-1"):
            raw = json.dumps(opening_hours_jsonld(schedules[uid]))
            assert isinstance(json.loads(raw), list)

    def test_adliswil_weekday_split_with_season_bounds(self):
        """JSON-LD keeps Mo–Fr / Sa–So as separate day specs (UI nesting is display-only)."""
        specs = opening_hours_jsonld(load_schedules()["IMBAD-1"])
        open_specs = [s for s in specs if "dayOfWeek" in s]
        assert len(open_specs) == 7
        by_day = {s["dayOfWeek"].rsplit("/", 1)[-1]: s for s in open_specs}
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            assert by_day[day]["opens"] == "07:30:00"
            assert by_day[day]["closes"] == "20:00:00"
            assert by_day[day]["validFrom"] == "2026-05-09"
            assert by_day[day]["validThrough"] == "2026-09-15"
        for day in ("Saturday", "Sunday"):
            assert by_day[day]["opens"] == "08:00:00"
            assert by_day[day]["closes"] == "20:00:00"
            assert by_day[day]["validFrom"] == "2026-05-09"
            assert by_day[day]["validThrough"] == "2026-09-15"
        # No fair_weather invention; no duplicate date-only rows
        assert all(s.get("opens") != "00:00:00" for s in open_specs)


class TestOpeningHoursFaqText:
    def test_fair_weather_wording(self):
        text = opening_hours_faq_text(
            _fair_weather_schedule(),
            "Freibad Allenmoos",
            when=_when(2026, 8, 4, 12),
        )
        assert "sicher" in text
        assert "täglich" in text
        assert "09:00" in text and "14:00" in text
        assert "schönem Wetter" in text
        assert "20:00" in text and "21:00" in text
        assert "finden Sie" in text
        # Must not claim 21:00 as the unconditional close
        assert "bis 21:00 Uhr sicher" not in text

    def test_always_only_shorter(self):
        schedule = PoolSchedule(
            uid="indoor",
            periods=(
                Period(
                    start=date(2026, 1, 1),
                    end=date(2026, 12, 31),
                    days=frozenset(range(7)),
                    intervals=(Interval(6 * 60 + 30, 22 * 60, "always"),),
                ),
            ),
        )
        text = opening_hours_faq_text(
            schedule, "Hallenbad Test", when=_when(2026, 8, 4, 12)
        )
        assert "sicher" in text
        assert "schönem Wetter" not in text
        assert "06:30" in text

    def test_weekday_varying_does_not_claim_taeglich_soup(self):
        """Käferberg-style schedules must not invent a false daily union of windows."""
        schedules = load_schedules()
        # Outside Revision (2026-07-27–2026-08-17) so FAQ describes usual hours.
        text = opening_hours_faq_text(
            schedules["SSD-6"], "Hallenbad Käferberg", when=_when(2026, 9, 1, 12)
        )
        assert "variieren nach Wochentag" in text
        assert "täglich" not in text
        assert "06:00–08:00" not in text
        assert "finden Sie" in text

    def test_active_closure_leads_faq(self):
        schedules = load_schedules()
        text = opening_hours_faq_text(
            schedules["SSD-4"], "Hallenbad City", when=_when(2026, 8, 4, 12)
        )
        assert "derzeit geschlossen" in text
        assert "Revision" in text
        assert "sicher geöffnet" not in text

    def test_off_season_does_not_claim_seasonal_hours(self):
        """Allenmoos in November must not emit in-season 'sicher geöffnet' FAQ."""
        schedules = load_schedules()
        text = opening_hours_faq_text(
            schedules["fb006"],
            "Freibad Allenmoos",
            when=_when(2026, 11, 15, 12),
        )
        assert "derzeit geschlossen" in text
        assert "sicher geöffnet" not in text
        assert "Mai" in text
        assert "finden Sie" in text

    def test_faq_scopes_day_set_to_current_period(self):
        """May Mo–Fr + June daily must not invent 'täglich' while still in May."""
        schedule = PoolSchedule(
            uid="split",
            periods=(
                Period(
                    start=date(2026, 5, 1),
                    end=date(2026, 5, 31),
                    days=frozenset(range(5)),  # Mo–Fr
                    intervals=(Interval(9 * 60, 14 * 60, "always"),),
                ),
                Period(
                    start=date(2026, 6, 1),
                    end=date(2026, 6, 30),
                    days=frozenset(range(7)),  # daily
                    intervals=(Interval(9 * 60, 14 * 60, "always"),),
                ),
            ),
            confidence="official_structured",
            scraped_at=date(2026, 5, 1),
        )
        may = opening_hours_faq_text(schedule, "Testbad", when=_when(2026, 5, 15, 12))
        assert "täglich" not in may
        assert "an Öffnungstagen" in may or "variieren" in may

        june = opening_hours_faq_text(schedule, "Testbad", when=_when(2026, 6, 15, 12))
        assert "täglich" in june
        assert "sicher" in june
