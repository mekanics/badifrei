"""Hours display view — Schedule → detail-page table (not legacy flat metadata)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ml.features import load_pool_metadata
from ml.opening_hours import (
    Interval,
    Period,
    PoolSchedule,
    hours_display_view,
    load_schedules,
    opening_hours_jsonld,
    opening_hours_summary_from_schedule,
)

TEMPLATES = Path(__file__).resolve().parents[2] / "api" / "templates"
_WHEN = datetime(2026, 8, 7, 12, 0, tzinfo=ZoneInfo("Europe/Zurich"))


class TestHoursDisplayViewSsd3:
    def test_weekday_kind_and_monday_split(self):
        schedule = load_schedules()["SSD-3"]
        view = hours_display_view(schedule, date(2026, 8, 7))
        assert view is not None
        assert view.kind == "weekday_table"
        mon = view.days[0]
        assert mon.closed is False
        assert mon.always == ("12:00–13:30", "16:00–19:00")
        assert "12:00–19:00" not in mon.always

    def test_friday_saturday_closed(self):
        schedule = load_schedules()["SSD-3"]
        view = hours_display_view(schedule, date(2026, 8, 7))
        assert view.days[4].closed is True  # Fri
        assert view.days[5].closed is True  # Sat
        assert view.days[4].always == ()
        assert view.days[5].always == ()

    def test_thursday_starts_1230(self):
        schedule = load_schedules()["SSD-3"]
        view = hours_display_view(schedule, date(2026, 8, 7))
        thu = view.days[3]
        assert thu.always == ("12:30–19:00",)

    def test_wednesday_merges_abutting_always(self):
        """Abutting same-condition windows may merge for display only."""
        schedule = load_schedules()["SSD-3"]
        view = hours_display_view(schedule, date(2026, 8, 7))
        wed = view.days[2]
        assert wed.always == ("12:00–19:00",)

    def test_summary_lists_split_not_minmax(self):
        schedule = load_schedules()["SSD-3"]
        summary = opening_hours_summary_from_schedule(schedule, date(2026, 8, 7))
        assert summary is not None
        assert "12:00–13:30, 16:00–19:00" in summary
        assert "Mo–Do: 12:00–19:00" not in summary


class TestHoursDisplayViewSeasonal:
    def test_allenmoos_seasonal_periods(self):
        schedule = load_schedules()["fb006"]
        view = hours_display_view(schedule, date(2026, 8, 7))
        assert view is not None
        assert view.kind == "seasonal_periods"
        assert view.current is not None
        assert view.has_fair_weather is True
        assert len(view.all_periods) > 1
        assert view.current.is_uniform_daily
        assert "·" not in view.current.label
        assert view.current.day_groups[0].always == "09:00–14:00"
        assert view.current.day_groups[0].fair == "14:00–21:00"

    def test_adliswil_one_period_nested_day_groups(self):
        """Same season dates → one Zeitraum with Mo–Fr / Sa–So nested."""
        schedule = load_schedules()["IMBAD-1"]
        view = hours_display_view(schedule, date(2026, 8, 8))
        assert view is not None
        assert view.kind == "seasonal_periods"
        assert len(view.all_periods) == 1
        period = view.all_periods[0]
        assert period.label == "9. Mai–15. Sep"
        assert period.is_uniform_daily is False
        by_days = {g.days_label: g.always for g in period.day_groups}
        assert by_days == {"Mo–Fr": "07:30–20:00", "Sa–So": "08:00–20:00"}
        assert view.current is period

    def test_adliswil_summary_nests_days(self):
        schedule = load_schedules()["IMBAD-1"]
        summary = opening_hours_summary_from_schedule(schedule, date(2026, 8, 7))
        assert summary is not None
        assert summary.startswith("9. Mai–15. Sep:")
        assert "Mo–Fr: 07:30–20:00" in summary
        assert "Sa–So: 08:00–20:00" in summary

    def test_allenmoos_off_season_highlights_next_not_as_current(self):
        """No Period covers today → next/first season block, not 'Aktuell'."""
        schedule = load_schedules()["fb006"]
        view = hours_display_view(schedule, date(2026, 11, 15))
        assert view is not None
        assert view.kind == "seasonal_periods"
        assert view.current_covers_today is False
        assert view.current is not None
        assert view.current.label == "9. Mai–29. Mai"

    def test_mid_season_gap_picks_upcoming_period(self):
        schedule = PoolSchedule(
            uid="gap-season",
            periods=(
                Period(
                    start=date(2026, 5, 1),
                    end=date(2026, 5, 31),
                    days=frozenset(range(7)),
                    intervals=(Interval(9 * 60, 18 * 60, "always"),),
                ),
                Period(
                    start=date(2026, 7, 1),
                    end=date(2026, 8, 31),
                    days=frozenset(range(7)),
                    intervals=(Interval(9 * 60, 20 * 60, "always"),),
                ),
            ),
        )
        view = hours_display_view(schedule, date(2026, 6, 15))
        assert view is not None
        assert view.current_covers_today is False
        assert view.current is not None
        assert view.current.label == "1. Jul–31. Aug"


class TestHoursDisplayViewMerge:
    def test_gap_not_merged(self):
        schedule = PoolSchedule(
            uid="gap",
            periods=(
                Period(
                    start=None,
                    end=None,
                    days=frozenset({0}),
                    intervals=(
                        Interval(12 * 60, 13 * 60 + 30, "always"),
                        Interval(16 * 60, 19 * 60, "always"),
                    ),
                ),
            ),
        )
        view = hours_display_view(schedule, date(2026, 8, 3))
        assert view.kind == "weekday_table"
        assert view.days[0].always == ("12:00–13:30", "16:00–19:00")

    def test_mixed_dated_and_evergreen_off_season_uses_weekday_table(self):
        """Eng-review edge #5: no covering dated Period → evergreen weekday view."""
        schedule = PoolSchedule(
            uid="mixed",
            periods=(
                Period(
                    start=date(2026, 5, 1),
                    end=date(2026, 9, 1),
                    days=frozenset(range(7)),
                    intervals=(Interval(9 * 60, 14 * 60, "always"),),
                ),
                Period(
                    start=None,
                    end=None,
                    days=frozenset({0}),
                    intervals=(Interval(6 * 60, 22 * 60, "always"),),
                ),
            ),
        )
        view = hours_display_view(schedule, date(2026, 12, 1))
        assert view is not None
        assert view.kind == "weekday_table"
        assert view.days[0].always == ("06:00–22:00",)


class TestSsd3PoolHtmlUsesSchedule:
    def test_html_shows_split_not_legacy_continuous(self):
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        from api.main import _fmt_date_de, _static_ver
        from ml.opening_hours import opening_hours_faq_text

        uid = "SSD-3"
        pool = load_pool_metadata()[uid]
        schedule = load_schedules()[uid]
        hours_view = hours_display_view(schedule, date(2026, 8, 7))

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        env.globals["static_ver"] = _static_ver
        env.globals["umami_script_url"] = ""
        env.globals["umami_website_id"] = ""
        env.filters["date_de"] = _fmt_date_de

        html = env.get_template("pool.html").render(
            pool=pool,
            schema_description="test",
            hours_jsonld=opening_hours_jsonld(schedule),
            hours_faq=opening_hours_faq_text(schedule, pool["name"], when=_WHEN),
            hours_view=hours_view,
            quietest_hour=10,
            related_pools=[],
            related_pools_heading="",
            active_closure=None,
            hours_confidence=schedule.confidence,
            hours_scraped_at=None,
            opening_hours_summary=None,
            weekly_insights=None,
            today_date="2026-08-07",
            today_prediction_status="ok",
            today_predictions_json="[]",
        )
        assert "12:00–13:30" in html
        assert "16:00–19:00" in html
        assert "12:00–13:30 · 16:00–19:00" in html
        # Legacy flat table used spaced en-dash: "12:00 – 19:00" for Mon–Thu
        assert "12:00 – 19:00" not in html

    def test_adliswil_html_one_period_two_day_rows(self):
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        from api.main import _fmt_date_de, _static_ver
        from ml.opening_hours import opening_hours_faq_text

        uid = "IMBAD-1"
        pool = load_pool_metadata()[uid]
        schedule = load_schedules()[uid]
        hours_view = hours_display_view(schedule, date(2026, 8, 8))

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        env.globals["static_ver"] = _static_ver
        env.globals["umami_script_url"] = ""
        env.globals["umami_website_id"] = ""
        env.filters["date_de"] = _fmt_date_de

        html = env.get_template("pool.html").render(
            pool=pool,
            schema_description="test",
            hours_jsonld=opening_hours_jsonld(schedule),
            hours_faq=opening_hours_faq_text(schedule, pool["name"], when=_WHEN),
            hours_view=hours_view,
            quietest_hour=10,
            related_pools=[],
            related_pools_heading="",
            active_closure=None,
            hours_confidence=schedule.confidence,
            hours_scraped_at=None,
            opening_hours_summary=None,
            weekly_insights=None,
            today_date="2026-08-08",
            today_prediction_status="ok",
            today_predictions_json="[]",
        )
        assert html.count("9. Mai–15. Sep") == 1
        assert "Mo–Fr" in html
        assert "Sa–So" in html
        assert "07:30–20:00" in html
        assert "08:00–20:00" in html
        assert "Alle Zeiträume" not in html
