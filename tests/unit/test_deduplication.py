"""Tests for collector deduplication logic."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock


def make_readings(fills: dict[str, int]) -> list:
    """Create pool readings with given uid→fill mapping."""
    from collector.ws_client import PoolReading
    return [
        PoolReading(
            uid=uid,
            name=f"Pool {uid}",
            currentfill=fill,
            maxspace=100,
            freespace=100 - fill,
        )
        for uid, fill in fills.items()
    ]


class TestShouldWrite:
    def test_write_when_state_empty(self):
        """Always write on first reading."""
        from collector.main import should_write
        readings = make_readings({"SSD-1": 30, "SSD-2": 45})
        assert should_write(readings, {}, None) is True

    def test_write_when_fill_changed(self):
        """Write if any pool fill has changed."""
        from collector.main import should_write
        readings = make_readings({"SSD-1": 35, "SSD-2": 45})  # SSD-1 changed
        last_state = {"SSD-1": 30, "SSD-2": 45}
        recent = datetime.now(timezone.utc) - timedelta(seconds=60)
        assert should_write(readings, last_state, recent) is True

    def test_no_write_when_unchanged_and_recent(self):
        """Skip write if nothing changed and last write was recent."""
        from collector.main import should_write
        readings = make_readings({"SSD-1": 30, "SSD-2": 45})
        last_state = {"SSD-1": 30, "SSD-2": 45}
        recent = datetime.now(timezone.utc) - timedelta(seconds=60)
        assert should_write(readings, last_state, recent) is False

    def test_force_write_after_interval(self):
        """Write if unchanged but 15+ minutes have passed."""
        from collector.main import should_write
        readings = make_readings({"SSD-1": 30, "SSD-2": 45})
        last_state = {"SSD-1": 30, "SSD-2": 45}
        old_write = datetime.now(timezone.utc) - timedelta(seconds=901)
        assert should_write(readings, last_state, old_write, force_interval_seconds=900) is True

    def test_write_on_new_pool_uid(self):
        """Write if a new pool uid appears that wasn't in last state."""
        from collector.main import should_write
        readings = make_readings({"SSD-1": 30, "SSD-NEW": 50})
        last_state = {"SSD-1": 30}  # SSD-NEW not seen before
        recent = datetime.now(timezone.utc) - timedelta(seconds=60)
        assert should_write(readings, last_state, recent) is True

    def test_unchanged_partial_match(self):
        """If last state has fewer pools but all match, don't write."""
        from collector.main import should_write
        readings = make_readings({"SSD-1": 30})
        last_state = {"SSD-1": 30}
        recent = datetime.now(timezone.utc) - timedelta(seconds=60)
        assert should_write(readings, last_state, recent) is False
