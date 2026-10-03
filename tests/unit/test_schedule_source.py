"""Runtime Schedule source — fetch, guards, and API/feature seams."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from ml.opening_hours import (
    GENERATED_PATH,
    METADATA_PATH,
    _legacy_to_schedule,
    load_schedules,
    resolve,
)
from ml.schedule_source import (
    MAX_DOCUMENT_BYTES,
    HttpResponse,
    ScheduleSource,
    _capped_body,
    _default_http_get,
    current_schedules,
    default_source,
    run_refresh_loop,
)

Z = ZoneInfo("Europe/Zurich")
MONDAY_2030 = datetime(2026, 2, 2, 20, 30, tzinfo=Z)
URL = "https://example.test/opening_hours.generated.json"


def _baked_doc() -> dict:
    return json.loads(GENERATED_PATH.read_text(encoding="utf-8"))


def _changed_doc() -> dict:
    doc = copy.deepcopy(_baked_doc())
    for pool in doc["pools"]:
        if pool["uid"] != "SSD-2":
            continue
        for period in pool.get("periods") or []:
            if period.get("days") == ["Mon"]:
                for interval in period.get("intervals") or []:
                    interval["close"] = "21:00"
    return doc


def _body(doc: dict) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _source(http_get=None) -> ScheduleSource:
    return ScheduleSource(http_get=http_get)


class TestScheduleSource:
    def test_baked_by_default(self):
        source = _source()
        snap = source.current()
        baked = load_schedules()
        assert snap.origin == "baked"
        assert set(snap.schedules) == set(baked)
        assert snap.schedules["SSD-2"].periods == baked["SSD-2"].periods

    def test_valid_remote_swaps_in(self):
        source = _source(lambda url: HttpResponse(200, _body(_changed_doc())))
        before = resolve(source.current().schedules["SSD-2"], MONDAY_2030)
        assert before.is_open is False
        assert source.refresh(URL) == "updated"
        after = resolve(source.current().schedules["SSD-2"], MONDAY_2030)
        assert after.is_open is True
        assert source.current().origin == "remote"

    def test_identical_body_unchanged(self):
        body = GENERATED_PATH.read_bytes()
        source = _source(lambda url: HttpResponse(200, body))
        assert source.refresh(URL) == "unchanged"
        assert source.refresh(URL) == "unchanged"
        sha = source.current().content_sha256
        assert source.refresh(URL) == "unchanged"
        assert source.current().content_sha256 == sha

    def test_non_200_is_fetch_failed(self):
        source = _source(lambda url: HttpResponse(404, b"missing"))
        baked_open = resolve(source.current().schedules["SSD-2"], MONDAY_2030).is_open
        assert source.refresh(URL) == "fetch_failed"
        assert (
            resolve(source.current().schedules["SSD-2"], MONDAY_2030).is_open
            is baked_open
        )

    def test_exception_never_escapes(self):
        def boom(_url):
            raise httpx.ConnectTimeout("down")

        source = _source(boom)
        assert source.refresh(URL) == "fetch_failed"

        def boom2(_url):
            raise RuntimeError("nope")

        source = _source(boom2)
        assert source.refresh(URL) == "fetch_failed"

    def test_oversize_rejected(self):
        source = _source(lambda url: HttpResponse(200, b"x" * (MAX_DOCUMENT_BYTES + 1)))
        origin = source.current().origin
        assert source.refresh(URL) == "rejected"
        assert source.current().origin == origin

    def test_non_json_rejected(self):
        source = _source(lambda url: HttpResponse(200, b"<html>nope</html>"))
        assert source.refresh(URL) == "rejected"
        assert source.status()["last_error"]

    @pytest.mark.parametrize("payload", [{"pools": []}, {}, []])
    def test_empty_or_missing_pools_rejected(self, payload):
        source = _source(lambda url: HttpResponse(200, json.dumps(payload).encode()))
        assert source.refresh(URL) == "rejected"

    def test_bad_pool_falls_back_others_update(self):
        doc = _changed_doc()
        for pool in doc["pools"]:
            if pool["uid"] != "SSD-3":
                continue
            for period in pool.get("periods") or []:
                for interval in period.get("intervals") or []:
                    interval["open"] = "not-a-time"
                    break
        source = _source(lambda url: HttpResponse(200, _body(doc)))
        assert source.refresh(URL) == "updated"
        assert resolve(source.current().schedules["SSD-2"], MONDAY_2030).is_open is True
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        ssd3 = next(p for p in metadata if p["uid"] == "SSD-3")
        expected = _legacy_to_schedule("SSD-3", ssd3["opening_hours"])
        assert source.current().schedules["SSD-3"].periods == expected.periods

    def test_last_good_survives_failure(self):
        state = {"ok": True}

        def http_get(_url):
            if state["ok"]:
                return HttpResponse(200, _body(_changed_doc()))
            raise httpx.ConnectTimeout("down")

        source = _source(http_get)
        assert source.refresh(URL) == "updated"
        state["ok"] = False
        assert source.refresh(URL) == "fetch_failed"
        assert source.current().origin == "remote"
        assert resolve(source.current().schedules["SSD-2"], MONDAY_2030).is_open is True


class TestDefaultHttpGet:
    def test_capped_body_stops_reading_after_limit(self):
        yielded: list[int] = []

        def chunks():
            for i in range(100):
                yielded.append(i)
                yield b"x" * 100_000

        body = _capped_body(chunks())
        assert len(body) > MAX_DOCUMENT_BYTES
        assert len(yielded) < 100

    def test_capped_body_keeps_bytes_when_content_length_unparseable(self):
        assert _capped_body([b"ab", b"cd"], content_length="nope") == b"abcd"

    def test_capped_body_skips_read_when_content_length_exceeds_cap(self):
        read = False

        def chunks():
            nonlocal read
            read = True
            yield b"hello"

        body = _capped_body(chunks(), content_length=str(MAX_DOCUMENT_BYTES + 1))
        assert read is False
        assert len(body) > MAX_DOCUMENT_BYTES

    def test_follows_redirects(self):
        final = GENERATED_PATH.read_bytes()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/from":
                return httpx.Response(
                    302,
                    headers={
                        "location": (
                            "https://example.test/opening_hours.generated.json"
                        )
                    },
                )
            return httpx.Response(200, content=final)

        got = _default_http_get(
            "https://example.test/from",
            transport=httpx.MockTransport(handler),
        )
        assert got.status == 200
        assert got.body == final

    def test_content_length_over_cap_does_not_read_body(self):
        class BoomStream(httpx.SyncByteStream):
            def __iter__(self):
                raise AssertionError("body should not be read")
                yield b"x"  # pragma: no cover

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-length": str(MAX_DOCUMENT_BYTES + 1)},
                stream=BoomStream(),
            )

        got = _default_http_get(URL, transport=httpx.MockTransport(handler))
        assert got.status == 200
        assert len(got.body) > MAX_DOCUMENT_BYTES
        source = _source(lambda url: got)
        assert source.refresh(URL) == "rejected"


class TestScheduleSourceSeams:
    def test_api_current_seam_reads_swapped_schedule(self, monkeypatch):
        from api.catalog import get_pools
        from api.snapshots import _compute_pool_is_open

        source = _source(lambda url: HttpResponse(200, _body(_changed_doc())))
        source.refresh(URL)
        monkeypatch.setattr("ml.schedule_source._default", source)
        pool = next(p for p in get_pools() if p["uid"] == "SSD-2")
        status = _compute_pool_is_open(pool, MONDAY_2030)
        assert status["is_open"] is True

    @pytest.mark.asyncio
    async def test_pool_page_shows_swapped_close(self, monkeypatch):
        from api.main import app
        from api.predictor import predictor

        source = _source(lambda url: HttpResponse(200, _body(_changed_doc())))
        source.refresh(URL)
        monkeypatch.setattr("ml.schedule_source._default", source)
        monkeypatch.setattr(predictor, "is_loaded", lambda: False)

        async def _predict_range_batch(pool_uid, hours, db_pool=None):
            return [0.0] * len(hours)

        monkeypatch.setattr(predictor, "predict_range_batch", _predict_range_batch)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/bad/SSD-2")
        assert response.status_code == 200
        assert "21:00" in response.text

    def test_forecast_features_read_through_source(self, monkeypatch):
        from ml.features import add_opening_hours_features, add_time_features

        df = add_time_features(
            pd.DataFrame(
                {
                    "time": [pd.Timestamp("2026-02-02 20:30:00", tz="Europe/Zurich")],
                    "pool_uid": ["SSD-2"],
                    "occupancy_pct": [50.0],
                }
            )
        )
        baked = add_opening_hours_features(df.copy(), None)
        assert int(baked.iloc[0]["is_open"]) == 0

        source = _source(lambda url: HttpResponse(200, _body(_changed_doc())))
        source.refresh(URL)
        monkeypatch.setattr("ml.schedule_source._default", source)
        swapped = add_opening_hours_features(df.copy(), None)
        assert int(swapped.iloc[0]["is_open"]) == 1

    @pytest.mark.asyncio
    async def test_health_exposes_hours_and_stays_200(self, monkeypatch):
        from api.main import app

        source = _source(
            lambda url: (_ for _ in ()).throw(httpx.ConnectTimeout("down"))
        )
        assert source.refresh(URL) == "fetch_failed"
        monkeypatch.setattr("ml.schedule_source._default", source)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["hours"]["last_result"] == "fetch_failed"

    def test_lifespan_starts_loop_only_when_configured(self, monkeypatch):
        from api.config import get_settings

        seen: list[int] = []

        def counting_refresh(url: str):
            seen.append(1)
            return "unchanged"

        monkeypatch.setenv("HOURS_SYNC_URL", URL)
        get_settings.cache_clear()
        source = default_source()
        monkeypatch.setattr(source, "refresh", counting_refresh)
        from api.main import app

        with TestClient(app):
            for _ in range(50):
                if seen:
                    break
                import time

                time.sleep(0.02)
        assert seen

        seen.clear()
        monkeypatch.setenv("HOURS_SYNC_URL", "")
        get_settings.cache_clear()
        import ml.schedule_source as ss

        ss._default = None
        source = default_source()
        monkeypatch.setattr(source, "refresh", counting_refresh)
        with TestClient(app):
            import time

            time.sleep(0.1)
        assert seen == []

    @pytest.mark.asyncio
    async def test_loop_retries_and_cancels_cleanly(self):
        calls: list[str] = []
        state = {"n": 0}

        def http_get(_url):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("down")
            return HttpResponse(200, GENERATED_PATH.read_bytes())

        source = _source(http_get)
        original = source.refresh

        def counted(url: str):
            result = original(url)
            calls.append(result)
            return result

        source.refresh = counted  # type: ignore[method-assign]
        task = asyncio.create_task(run_refresh_loop(source, URL, 0.01))
        for _ in range(50):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) >= 2
        assert "fetch_failed" in calls

    def test_unit_suite_isolated(self):
        from api.config import get_settings

        assert get_settings().hours_sync_url == ""
        assert default_source().current().origin == "baked"
        assert default_source().current().schedules is current_schedules()
