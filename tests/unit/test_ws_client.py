"""Tests for WebSocket client module."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collector.ws_client import PoolReading, parse_message


class TestPoolReading:
    def test_parse_valid_record(self):
        data = {"uid": "SSD-5", "name": "Waermebad Kaeferberg", "currentfill": 27, "maxspace": 70, "freespace": 43}
        r = PoolReading(**data)
        assert r.uid == "SSD-5"
        assert r.currentfill == 27
        assert r.maxspace == 70

    def test_currentfill_as_string(self):
        """API sometimes returns currentfill as string."""
        data = {"uid": "SSD-5", "name": "Test", "currentfill": "27", "maxspace": 70, "freespace": 43}
        r = PoolReading(**data)
        assert r.currentfill == 27

    def test_negative_currentfill_clamped_to_zero(self):
        data = {"uid": "SSD-5", "name": "Test", "currentfill": -5, "maxspace": 70, "freespace": 75}
        r = PoolReading(**data)
        assert r.currentfill == 0

    def test_negative_freespace_clamped_to_zero(self):
        data = {"uid": "SSD-5", "name": "Test", "currentfill": 80, "maxspace": 70, "freespace": -10}
        r = PoolReading(**data)
        assert r.freespace == 0


class TestParseMessage:
    def test_parse_valid_message(self):
        payload = [
            {"uid": "SSD-5", "name": "Kaeferberg", "currentfill": "27", "maxspace": 70, "freespace": 43},
            {"uid": "SSD-4", "name": "City", "currentfill": "99", "maxspace": 220, "freespace": 121},
        ]
        readings = parse_message(json.dumps(payload))
        assert len(readings) == 2
        assert readings[0].uid == "SSD-5"

    def test_parse_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_message("not valid json")

    def test_parse_non_list_raises(self):
        with pytest.raises(ValueError):
            parse_message(json.dumps({"uid": "SSD-5"}))

    def test_parse_skips_records_missing_uid(self):
        payload = [
            {"name": "No UID", "currentfill": 5, "maxspace": 70, "freespace": 65},
            {"uid": "SSD-5", "name": "Valid", "currentfill": 27, "maxspace": 70, "freespace": 43},
        ]
        readings = parse_message(json.dumps(payload))
        assert len(readings) == 1
        assert readings[0].uid == "SSD-5"

    def test_parse_empty_list(self):
        readings = parse_message(json.dumps([]))
        assert readings == []

    def test_parse_full_api_response(self):
        """Test with realistic 22-pool payload."""
        payload = [
            {"uid": f"POOL-{i}", "name": f"Pool {i}", "currentfill": str(i*2), "maxspace": 100, "freespace": 100-i*2}
            for i in range(22)
        ]
        readings = parse_message(json.dumps(payload))
        assert len(readings) == 22
