"""fresh_water_temp: both freshness gates + value clamping."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from api.water_temperature import WATER_TEMP_MAX_SOURCE_AGE, fresh_water_temp

Z = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 8, 8, 14, 0, tzinfo=Z)


def _fresh(**overrides):
    """Baseline: both gates pass, value in range."""
    defaults = {
        "water_temp_c": 26.0,
        "observed_at": NOW - timedelta(minutes=6),
        "source_modified_at": NOW - timedelta(hours=9),
        "now": NOW,
    }
    defaults.update(overrides)
    return fresh_water_temp(
        defaults["water_temp_c"],
        observed_at=defaults["observed_at"],
        source_modified_at=defaults["source_modified_at"],
        now=defaults["now"],
    )


class TestFreshWaterTemp:
    def test_returns_value_when_both_gates_pass(self):
        assert _fresh() == 26.0

    def test_suppressed_when_observed_at_over_60_minutes(self):
        assert _fresh(observed_at=NOW - timedelta(minutes=61)) is None

    def test_suppressed_when_source_modified_at_over_7_days(self):
        assert _fresh(source_modified_at=NOW - timedelta(days=8)) is None

    def test_suppressed_when_source_modified_at_is_null(self):
        assert _fresh(source_modified_at=None) is None

    def test_suppressed_when_observed_at_is_null(self):
        assert _fresh(observed_at=None) is None

    def test_suppressed_when_water_temp_is_null(self):
        assert _fresh(water_temp_c=None) is None

    def test_23h_source_age_still_renders(self):
        """Regression: short source_modified_at cutoffs false-hide live temps.

        Measured Baditicker update gaps reach 23.4h in peak season. A 48h
        (or shorter) cutoff keyed only on source_modified_at would hide
        perfectly live temperatures. Both-gates rule must keep this visible.
        """
        assert _fresh(source_modified_at=NOW - timedelta(hours=23, minutes=24)) == 26.0

    def test_exactly_at_7_day_boundary_still_renders(self):
        assert _fresh(source_modified_at=NOW - WATER_TEMP_MAX_SOURCE_AGE) == 26.0

    def test_suppressed_when_below_sane_range(self):
        assert _fresh(water_temp_c=-1.0) is None

    def test_suppressed_when_above_sane_range(self):
        assert _fresh(water_temp_c=41.0) is None

    def test_boundary_temps_in_range_pass(self):
        assert _fresh(water_temp_c=0.0) == 0.0
        assert _fresh(water_temp_c=40.0) == 40.0
