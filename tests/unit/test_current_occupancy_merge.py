"""/api/current must include every known pool, even without occupancy rows."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from api.main import _merge_current_pool_items

Z = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 8, 7, 22, 0, tzinfo=Z)


def _status_closed_revision(_pool, _now, **_kwargs):
    return {
        "is_open": False,
        "next_open": "06:00",
        "opens_seasonal": None,
        "state": "closed_exception",
        "reason": "Revision bis 16. Aug",
        "confidence": "official_structured",
    }


def _status_open(_pool, _now, **_kwargs):
    return {
        "is_open": True,
        "next_open": None,
        "opens_seasonal": None,
        "state": "open_guaranteed",
        "reason": None,
        "confidence": "official_structured",
    }


class TestMergeCurrentPoolItems:
    def test_pool_without_occupancy_still_emitted_with_status(self):
        pools = [
            {"uid": "SSD-1", "name": "Hallenbad Altstetten", "city": "zurich"},
            {"uid": "fb006", "name": "Freibad Allenmoos", "city": "zurich"},
        ]
        occupancy = {
            "fb006": {
                "pool_uid": "fb006",
                "current_fill": 100,
                "max_space": 200,
                "free_space": 100,
                "occupancy_pct": 50,
                "time": NOW,
            }
        }
        items = _merge_current_pool_items(
            pools,
            occupancy_by_uid=occupancy,
            observations={},
            weather_by_city={},
            now_zurich=NOW,
            compute_status=_status_closed_revision,
        )
        by_uid = {i["pool_uid"]: i for i in items}
        assert set(by_uid) == {"SSD-1", "fb006"}

        ssd1 = by_uid["SSD-1"]
        assert ssd1["occupancy_pct"] is None
        assert ssd1["current_fill"] is None
        assert ssd1["is_open"] is False
        assert ssd1["state"] == "closed_exception"
        assert "Revision" in ssd1["reason"]

        fb = by_uid["fb006"]
        assert fb["occupancy_pct"] == 50
        assert fb["current_fill"] == 100

    def test_unknown_occupancy_uid_ignored(self):
        """Occupancy for a uid not in metadata must not invent a card row."""
        pools = [{"uid": "SSD-1", "city": "zurich"}]
        occupancy = {
            "GHOST": {
                "pool_uid": "GHOST",
                "current_fill": 1,
                "max_space": 10,
                "free_space": 9,
                "occupancy_pct": 10,
                "time": NOW,
            }
        }
        items = _merge_current_pool_items(
            pools,
            occupancy_by_uid=occupancy,
            observations={},
            weather_by_city={},
            now_zurich=NOW,
            compute_status=_status_open,
        )
        assert [i["pool_uid"] for i in items] == ["SSD-1"]
        assert items[0]["occupancy_pct"] is None
