"""Tests for the Baditicker status poller."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from collector.status import (
    StatusReading,
    build_poiid_to_uid,
    parse_feed,
    parse_german_timestamp,
    poll_once,
    run_status_poller,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "baditicker_sample.xml"
METADATA_PATH = Path(__file__).parents[2] / "ml" / "pool_metadata.json"
ZURICH = ZoneInfo("Europe/Zurich")


@pytest.fixture
def fixture_xml() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def pools() -> list[dict]:
    return json.loads(METADATA_PATH.read_text(encoding="utf-8"))


class TestParseGermanTimestamp:
    def test_parses_with_weekday_prefix(self):
        result = parse_german_timestamp("Di., 04.08.2026 08:51")
        assert result == datetime(2026, 8, 4, 8, 51, tzinfo=ZURICH)

    def test_parses_without_weekday(self):
        result = parse_german_timestamp("04.08.2026 08:51")
        assert result == datetime(2026, 8, 4, 8, 51, tzinfo=ZURICH)

    def test_empty_returns_none(self):
        assert parse_german_timestamp("") is None
        assert parse_german_timestamp(None) is None

    def test_unparseable_returns_none(self):
        assert parse_german_timestamp("not a date") is None


class TestParseFeed:
    def test_parses_fixture(self, fixture_xml):
        readings = parse_feed(fixture_xml)
        by_poiid = {r.poiid: r for r in readings}
        assert "fb006" in by_poiid
        assert by_poiid["fb006"].status_text == "offen"
        assert by_poiid["fb006"].water_temp_c == 27.0
        assert by_poiid["fb006"].source_modified_at == datetime(
            2026, 8, 4, 8, 51, tzinfo=ZURICH
        )

    def test_empty_status_is_none_not_closed(self, fixture_xml):
        """Indoor pools with empty status must not be treated as closed."""
        readings = parse_feed(fixture_xml)
        city = next(r for r in readings if r.poiid == "hb001")
        assert city.status_text is None

    def test_geschlossen_preserved(self, fixture_xml):
        readings = parse_feed(fixture_xml)
        utoquai = next(r for r in readings if r.poiid == "seb6945")
        assert utoquai.status_text == "geschlossen"

    def test_skips_missing_poiid(self, fixture_xml):
        readings = parse_feed(fixture_xml)
        assert all(r.poiid for r in readings)

    def test_malformed_document_raises(self):
        with pytest.raises(Exception):
            parse_feed("<not-valid")


class TestPoiidCrosswalk:
    def test_every_declared_poiid_in_fixture_or_known_feed(self, pools, fixture_xml):
        """Every non-null baditicker_poiid must be a real Baditicker id.

        The fixture covers a sample; the full known feed ids are listed here so
        the crosswalk cannot silently invent poiids.
        """
        known_feed_poiids = {
            "flb6938",
            "flb6939",
            "flb6940",
            "flb8803",
            "flb6941",
            "fb006",
            "fb008",
            "fb016",
            "fb012",
            "fb002",
            "fb013",
            "fb018",
            "hb005",
            "hb002",
            "hb001",
            "hb006",
            "hb004",
            "flb6942",
            "seb6943",
            "seb6944",
            "seb6945",
            "seb6946",
            "seb6947",
            "seb6948",
            "hb007",
        }
        for pool in pools:
            assert "baditicker_poiid" in pool, f"{pool['uid']} missing baditicker_poiid"
            poiid = pool["baditicker_poiid"]
            if poiid is not None:
                assert poiid in known_feed_poiids, (
                    f"{pool['uid']} maps to unknown poiid {poiid}"
                )

    def test_key_mismatches_resolved(self, pools):
        by_uid = {p["uid"]: p["baditicker_poiid"] for p in pools}
        assert by_uid["LETZI-1"] == "fb002"
        assert by_uid["SSD-11"] == "fb013"
        assert by_uid["SSD-10"] == "seb6945"
        assert by_uid["BADI-1"] == "seb6943"
        assert by_uid["SSD-4"] == "hb001"
        assert by_uid["LIDO-1"] is None

    def test_build_poiid_to_uid(self, pools):
        mapping = build_poiid_to_uid(pools)
        assert mapping["fb002"] == "LETZI-1"
        assert mapping["seb6945"] == "SSD-10"
        assert "fb016" not in mapping  # untracked Dolder


class TestPollOnce:
    async def test_maps_and_writes_tracked_pools(self, fixture_xml, pools):
        with patch(
            "collector.status.write_status_batch",
            new_callable=AsyncMock,
            return_value=5,
        ) as mock_write:
            written = await poll_once(
                pools, fetch=AsyncMock(return_value=fixture_xml)
            )
        assert written == 5
        records = mock_write.call_args[0][0]
        uids = {r["pool_uid"] for r in records}
        assert "fb006" in uids
        assert "LETZI-1" in uids  # via fb002
        assert "SSD-10" in uids  # via seb6945
        # Untracked Dolder (fb016) must not appear
        assert all(r["baditicker_poiid"] != "fb016" for r in records)

    async def test_empty_status_written_as_none(self, fixture_xml, pools):
        with patch(
            "collector.status.write_status_batch",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_write:
            await poll_once(pools, fetch=AsyncMock(return_value=fixture_xml))
        city = next(r for r in mock_write.call_args[0][0] if r["pool_uid"] == "SSD-4")
        assert city["status_text"] is None


class TestPollerIsolation:
    async def test_poller_error_does_not_raise(self, pools):
        """A failing fetch must be swallowed so occupancy collection continues."""
        import asyncio

        shutdown = asyncio.Event()
        errors: list[Exception] = []

        async def boom(*_args, **_kwargs):
            raise RuntimeError("network down")

        async def stop_soon():
            await asyncio.sleep(0.05)
            shutdown.set()

        with patch("collector.status.poll_once", side_effect=boom), patch(
            "collector.config.settings"
        ) as mock_settings:
            mock_settings.status_poll_seconds = 1
            mock_settings.baditicker_url = "http://example.test"
            await asyncio.gather(
                run_status_poller(
                    pools,
                    shutdown=shutdown,
                    on_error=errors.append,
                ),
                stop_soon(),
            )
        assert errors
        assert isinstance(errors[0], RuntimeError)
