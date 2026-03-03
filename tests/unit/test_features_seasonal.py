"""Tests for seasonal closure and is_open post-processing gate."""
import pytest
from datetime import date, datetime, timezone
from ml.features import compute_opening_hours_for_row


# --- Test: seasonal Freibad returns is_open=0 outside seasonal window ---
def test_seasonal_closure_outside_window():
    """Freibad closed in winter should return is_open=0."""
    opening_hours = {
        "seasonal_open": "2026-05-09",
        "seasonal_close": "2026-09-20",
        "schedule": {
            "Mon": {"open": "09:00", "close": "20:00"},
        }
    }
    # January — outside seasonal window
    result = compute_opening_hours_for_row(
        hour=12, day_of_week=0, opening_hours=opening_hours,
        date=date(2026, 1, 15)
    )
    assert result == (0, 0, 0), f"Expected closed in winter, got {result}"


# --- Test: seasonal Freibad returns is_open=1 inside seasonal window ---
def test_seasonal_open_inside_window():
    """Freibad should be open during summer season."""
    opening_hours = {
        "seasonal_open": "2026-05-09",
        "seasonal_close": "2026-09-20",
        "schedule": {
            "Mon": {"open": "09:00", "close": "20:00"},
            "Tue": {"open": "09:00", "close": "20:00"},
            "Wed": {"open": "09:00", "close": "20:00"},
            "Thu": {"open": "09:00", "close": "20:00"},
            "Fri": {"open": "09:00", "close": "20:00"},
            "Sat": {"open": "09:00", "close": "20:00"},
            "Sun": {"open": "09:00", "close": "20:00"},
        }
    }
    # July — inside seasonal window, noon on Monday
    result = compute_opening_hours_for_row(
        hour=12, day_of_week=0, opening_hours=opening_hours,
        date=date(2026, 7, 6)  # Monday
    )
    is_open, since_open, until_close = result
    assert is_open == 1, f"Expected open in summer, got {result}"


# --- Test: no date passed → seasonal check skipped (backward compat) ---
def test_seasonal_check_skipped_without_date():
    """When no date is passed, seasonal check must be skipped (backward compat)."""
    opening_hours = {
        "seasonal_open": "2026-05-09",
        "seasonal_close": "2026-09-20",
        "schedule": {
            "Mon": {"open": "09:00", "close": "20:00"},
        }
    }
    # No date passed — should still work, returns based on schedule only
    result = compute_opening_hours_for_row(
        hour=12, day_of_week=0, opening_hours=opening_hours
    )
    # Should not crash — result depends on schedule
    assert isinstance(result, tuple) and len(result) == 3


# --- Test: regular closed hour still returns is_open=0 ---
def test_regular_closed_hour():
    """Pool closed at midnight should return is_open=0 regardless of season."""
    opening_hours = {
        "schedule": {
            "Mon": {"open": "09:00", "close": "20:00"},
        }
    }
    result = compute_opening_hours_for_row(
        hour=2, day_of_week=0, opening_hours=opening_hours,
        date=date(2026, 7, 6)
    )
    assert result == (0, 0, 0), f"Expected closed at 02:00, got {result}"
