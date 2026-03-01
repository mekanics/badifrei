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
