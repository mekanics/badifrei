"""Tests for opening_hours data in pool_metadata.json."""

import json
import pathlib
import pytest

METADATA_PATH = pathlib.Path(__file__).parents[2] / "ml" / "pool_metadata.json"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_pools():
    with open(METADATA_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def pools():
    return load_pools()


def test_all_pools_have_opening_hours(pools):
    """Every pool must have an opening_hours key."""
    for pool in pools:
        assert "opening_hours" in pool, f"Pool {pool['uid']} missing opening_hours"


def test_all_pools_have_schedule(pools):
    """Every pool's opening_hours must contain a schedule."""
    for pool in pools:
        oh = pool["opening_hours"]
        assert "schedule" in oh, f"Pool {pool['uid']} missing schedule"


def test_schedule_has_all_seven_days(pools):
    """Every schedule must have exactly the 7 day keys (Mon-Sun)."""
    for pool in pools:
        schedule = pool["opening_hours"]["schedule"]
        missing = [d for d in DAYS if d not in schedule]
        assert not missing, (
            f"Pool {pool['uid']} schedule missing days: {missing}"
        )


def test_open_before_close(pools):
    """For any day that has open/close times, open must be before close."""
    for pool in pools:
        schedule = pool["opening_hours"]["schedule"]
        for day, slot in schedule.items():
            if slot is None:
                continue  # closed day is fine
            assert "open" in slot, f"Pool {pool['uid']} {day} missing 'open'"
            assert "close" in slot, f"Pool {pool['uid']} {day} missing 'close'"
            open_t = slot["open"]
            close_t = slot["close"]
            assert open_t < close_t, (
                f"Pool {pool['uid']} {day}: open={open_t} is not before close={close_t}"
            )


def test_time_format(pools):
    """Time strings must be in HH:MM format."""
    import re
    pattern = re.compile(r"^\d{2}:\d{2}$")
    for pool in pools:
        schedule = pool["opening_hours"]["schedule"]
        for day, slot in schedule.items():
            if slot is None:
                continue
            for field in ("open", "close"):
                val = slot[field]
                assert pattern.match(val), (
                    f"Pool {pool['uid']} {day} {field}='{val}' not in HH:MM format"
                )


def test_seasonal_fields_present(pools):
    """seasonal_open and seasonal_close must be present in opening_hours."""
    for pool in pools:
        oh = pool["opening_hours"]
        assert "seasonal_open" in oh, f"Pool {pool['uid']} missing seasonal_open"
        assert "seasonal_close" in oh, f"Pool {pool['uid']} missing seasonal_close"


def test_seasonal_pools_have_dates(pools):
    """Pools marked seasonal=True must have non-null seasonal_open and seasonal_close."""
    for pool in pools:
        if pool.get("seasonal"):
            oh = pool["opening_hours"]
            assert oh["seasonal_open"] is not None, (
                f"Seasonal pool {pool['uid']} has null seasonal_open"
            )
            assert oh["seasonal_close"] is not None, (
                f"Seasonal pool {pool['uid']} has null seasonal_close"
            )


def test_year_round_pools_have_null_seasonal_dates(pools):
    """Non-seasonal pools must have null seasonal_open and seasonal_close."""
    for pool in pools:
        if not pool.get("seasonal"):
            oh = pool["opening_hours"]
            assert oh["seasonal_open"] is None, (
                f"Year-round pool {pool['uid']} has non-null seasonal_open: {oh['seasonal_open']}"
            )
            assert oh["seasonal_close"] is None, (
                f"Year-round pool {pool['uid']} has non-null seasonal_close: {oh['seasonal_close']}"
            )


def test_seasonal_open_before_close(pools):
    """seasonal_open date must be before seasonal_close date."""
    for pool in pools:
        oh = pool["opening_hours"]
        if oh["seasonal_open"] and oh["seasonal_close"]:
            assert oh["seasonal_open"] < oh["seasonal_close"], (
                f"Pool {pool['uid']}: seasonal_open={oh['seasonal_open']} "
                f"not before seasonal_close={oh['seasonal_close']}"
            )


# ---------------------------------------------------------------------------
# Tests for _compute_pool_is_open() minute-accurate logic
# ---------------------------------------------------------------------------

import zoneinfo  # noqa: E402
from datetime import datetime  # noqa: E402

_TZ = zoneinfo.ZoneInfo("Europe/Zurich")


def _make_pool(open_time: str, close_time: str, day: str = "Fri",
               seasonal_open=None, seasonal_close=None) -> dict:
    """Build a minimal pool dict with a fixed schedule for all seven days."""
    schedule = {}
    for d in DAYS:
        schedule[d] = {"open": open_time, "close": close_time}
    return {
        "uid": "test-pool",
        "name": "Test Pool",
        "opening_hours": {
            "seasonal_open": seasonal_open,
            "seasonal_close": seasonal_close,
            "schedule": schedule,
        },
    }


def _zurich(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_TZ)


class TestComputePoolIsOpen:
    """Unit tests for api.main._compute_pool_is_open()."""

    def _call(self, pool: dict, now: datetime) -> dict:
        from api.main import _compute_pool_is_open
        return _compute_pool_is_open(pool, now)

    # ── The exact bug case ──────────────────────────────────────────────────

    def test_pool_open_at_half_past_is_open_at_34_minutes(self):
        """Pool opens at 06:30; checked at 06:34 — must be open (was the bug)."""
        pool = _make_pool("06:30", "20:00")
        # 2026-03-20 is a Friday
        result = self._call(pool, _zurich(2026, 3, 20, 6, 34))
        assert result["is_open"] is True
        assert result["next_open"] is None

    def test_pool_open_at_half_past_is_open_at_exactly_opening_minute(self):
        """Pool opens at 06:30; checked at 06:30 exactly — must be open."""
        pool = _make_pool("06:30", "20:00")
        result = self._call(pool, _zurich(2026, 3, 20, 6, 30))
        assert result["is_open"] is True

    # ── One minute before opening ───────────────────────────────────────────

    def test_one_minute_before_open_shows_correct_opens_at_time(self):
        """Pool opens at 09:00; checked at 08:59 — closed with next_open='09:00'."""
        pool = _make_pool("09:00", "20:00")
        result = self._call(pool, _zurich(2026, 3, 20, 8, 59))
        assert result["is_open"] is False
        assert result["next_open"] == "09:00"

    def test_one_minute_before_half_past_open_shows_correct_opens_at(self):
        """Pool opens at 06:30; checked at 06:29 — closed with next_open='06:30'."""
        pool = _make_pool("06:30", "20:00")
        result = self._call(pool, _zurich(2026, 3, 20, 6, 29))
        assert result["is_open"] is False
        assert result["next_open"] == "06:30"

    # ── After closing ───────────────────────────────────────────────────────

    def test_after_close_shows_next_day_opening(self):
        """Pool closes at 20:00 on Friday; checked at 21:00 — should show Saturday opening."""
        pool = _make_pool("09:00", "20:00")
        # Friday 21:00 → tomorrow (Saturday) opens at 09:00 (time only, no day label)
        result = self._call(pool, _zurich(2026, 3, 20, 21, 0))
        assert result["is_open"] is False
        assert result["next_open"] == "09:00"

    def test_after_close_at_closing_minute_is_closed(self):
        """Pool closes at 20:00; checked at exactly 20:00 — must be closed."""
        pool = _make_pool("09:00", "20:00")
        result = self._call(pool, _zurich(2026, 3, 20, 20, 0))
        assert result["is_open"] is False

    # ── Mid-open sanity checks ──────────────────────────────────────────────

    def test_open_during_normal_hours(self):
        """Pool is open 09:00–20:00; checked at 14:00 — must be open."""
        pool = _make_pool("09:00", "20:00")
        result = self._call(pool, _zurich(2026, 3, 20, 14, 0))
        assert result["is_open"] is True

    def test_open_during_normal_hours_with_minutes(self):
        """Pool is open 09:00–20:00; checked at 14:45 — must be open."""
        pool = _make_pool("09:00", "20:00")
        result = self._call(pool, _zurich(2026, 3, 20, 14, 45))
        assert result["is_open"] is True

    # ── No opening hours ───────────────────────────────────────────────────

    def test_no_opening_hours_defaults_to_open(self):
        """Pool without opening_hours is treated as always open."""
        pool = {"uid": "test", "name": "Test", "opening_hours": None}
        result = self._call(pool, _zurich(2026, 3, 20, 14, 0))
        assert result["is_open"] is True

    # ── Seasonal window ────────────────────────────────────────────────────

    def test_off_season_returns_closed_with_seasonal_label(self):
        """Pool outside seasonal window shows opens_seasonal label, not next_open."""
        pool = _make_pool("09:00", "20:00",
                          seasonal_open="2026-05-01", seasonal_close="2026-09-30")
        # March is before May — off-season
        result = self._call(pool, _zurich(2026, 3, 20, 14, 0))
        assert result["is_open"] is False
        assert result["next_open"] is None
        assert result["opens_seasonal"] is not None
        assert "Mai" in result["opens_seasonal"]
