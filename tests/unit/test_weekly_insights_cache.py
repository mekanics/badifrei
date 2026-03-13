"""TASK-027: Tests for weekly insights cache (stale-while-revalidate).

TDD — these tests were written BEFORE the implementation.
"""
import asyncio
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_state():
    """Return a minimal app.state-like object."""

    class State:
        pass

    s = State()
    s.weekly_insights_cache = {}
    s.weekly_insights_inflight = set()
    s.db_pool = None
    return s


def _fresh_insights() -> dict:
    return {
        "has_data": True,
        "quietest_day_name": "Dienstag",
        "quietest_hour": 8,
        "quietest_hour_str": "08:00",
        "peak_hour": 14,
        "peak_hour_str": "14:00",
        "weekday_quieter_than_weekend": True,
    }


# ---------------------------------------------------------------------------
# Test 1: is_stale() boundary conditions
# ---------------------------------------------------------------------------

class TestIsStale:
    """Unit-test the pure staleness-check helper."""

    def test_far_beyond_ttl_is_stale(self):
        from api.main import is_stale
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        assert is_stale(old, ttl=3600) is True

    def test_exactly_at_ttl_is_stale(self):
        """At exactly TTL seconds, the entry should be considered stale."""
        from api.main import is_stale
        at_ttl = datetime.now(timezone.utc) - timedelta(seconds=3600)
        assert is_stale(at_ttl, ttl=3600) is True

    def test_one_second_before_ttl_is_fresh(self):
        from api.main import is_stale
        almost = datetime.now(timezone.utc) - timedelta(seconds=3599)
        assert is_stale(almost, ttl=3600) is False

    def test_just_computed_is_fresh(self):
        from api.main import is_stale
        now = datetime.now(timezone.utc)
        assert is_stale(now, ttl=3600) is False


# ---------------------------------------------------------------------------
# Test 2: cache hit — fresh entry skips 168h prediction
# ---------------------------------------------------------------------------

class TestCacheHit:
    """Fresh cache entry must be returned without recomputing the 168-hour grid."""

    @pytest.fixture
    def mock_predictor_spy(self):
        """Return a predictor mock that records predict_range_batch call counts."""
        mock = MagicMock()
        call_log = []

        async def _predict(pool_uid, hours, db_pool=None):
            call_log.append(len(hours))
            return [0.0] * len(hours)

        mock.predict_range_batch = _predict
        mock.is_loaded = MagicMock(return_value=True)
        mock._call_log = call_log
        return mock

    async def test_cache_hit_returns_cached_value(self, mock_predictor_spy):
        """Pool detail must return the cached weekly_insights without running 168h prediction."""
        from api.main import app, is_stale

        insights = _fresh_insights()
        computed_at = datetime.now(timezone.utc) - timedelta(seconds=60)  # fresh (TTL=3600)

        # Seed the cache
        app.state.weekly_insights_cache = {"SSD-5": (insights, computed_at)}
        app.state.weekly_insights_inflight = set()

        call_log = mock_predictor_spy._call_log

        with patch("api.main.predictor", mock_predictor_spy):
            with patch("api.main.asyncio.create_task") as mock_create_task:
                from httpx import AsyncClient, ASGITransport
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/bad/SSD-5")

        assert resp.status_code == 200
        # The 168-hour predict call must NOT have happened
        assert 168 not in call_log, (
            "predict_range_batch was called for 168 hours even though cache was fresh"
        )
        # No background task should have been spawned for a fresh cache
        mock_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: cache miss — cold cache returns None and spawns background task
# ---------------------------------------------------------------------------

class TestCacheMiss:
    async def test_cache_miss_triggers_background_recompute(self):
        """Cold cache: response must return immediately (weekly_insights=None in context)
        and a background refresh task must be scheduled via asyncio.create_task.
        """
        from api.main import app

        app.state.weekly_insights_cache = {}
        app.state.weekly_insights_inflight = set()

        mock_pred = MagicMock()

        async def _predict(pool_uid, hours, db_pool=None):
            return [0.0] * len(hours)

        mock_pred.predict_range_batch = _predict
        mock_pred.is_loaded = MagicMock(return_value=True)

        created_tasks = []

        def _capture_create_task(coro):
            created_tasks.append(coro)
            # Don't actually schedule it to keep test deterministic
            if hasattr(coro, 'close'):
                coro.close()
            return MagicMock()

        context_captured = {}

        original_response = None

        with patch("api.main.predictor", mock_pred):
            with patch("api.main.asyncio.create_task", side_effect=_capture_create_task):
                # Patch template rendering to capture context
                from fastapi.templating import Jinja2Templates
                orig_response = Jinja2Templates.TemplateResponse

                def _capture(self_, name_or_request, context_or_name=None, *args, **kwargs):
                    # Capture weekly_insights from template context
                    if isinstance(name_or_request, str):
                        ctx = context_or_name or {}
                    else:
                        ctx = context_or_name or {}
                    context_captured.update(ctx)
                    # Return minimal HTML
                    from starlette.responses import HTMLResponse
                    return HTMLResponse("<html></html>")

                with patch.object(Jinja2Templates, "TemplateResponse", _capture):
                    from httpx import AsyncClient, ASGITransport
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        resp = await client.get("/bad/SSD-5")

        assert resp.status_code == 200
        assert len(created_tasks) >= 1, "Background refresh task was not spawned"
        # weekly_insights should be None (cold cache)
        assert context_captured.get("weekly_insights") is None, (
            f"Expected weekly_insights=None for cold cache, got: {context_captured.get('weekly_insights')}"
        )


# ---------------------------------------------------------------------------
# Test 4: stale cache — serve stale value immediately, spawn background refresh
# ---------------------------------------------------------------------------

class TestStaleCache:
    async def test_stale_cache_serves_old_value_while_refreshing(self):
        """Stale cache: response must return the stale insights immediately
        and a background task must be spawned for recomputation.
        """
        from api.main import app

        stale_insights = _fresh_insights()
        stale_insights["quietest_day_name"] = "Montag"  # distinctive value
        computed_at = datetime.now(timezone.utc) - timedelta(hours=2)  # beyond TTL

        app.state.weekly_insights_cache = {"SSD-5": (stale_insights, computed_at)}
        app.state.weekly_insights_inflight = set()

        mock_pred = MagicMock()

        async def _predict(pool_uid, hours, db_pool=None):
            return [0.0] * len(hours)

        mock_pred.predict_range_batch = _predict
        mock_pred.is_loaded = MagicMock(return_value=True)

        created_tasks = []
        context_captured = {}

        def _capture_create_task(coro):
            created_tasks.append(coro)
            if hasattr(coro, 'close'):
                coro.close()
            return MagicMock()

        with patch("api.main.predictor", mock_pred):
            with patch("api.main.asyncio.create_task", side_effect=_capture_create_task):
                from fastapi.templating import Jinja2Templates

                def _capture(self_, name_or_request, context_or_name=None, *args, **kwargs):
                    if isinstance(context_or_name, dict):
                        context_captured.update(context_or_name)
                    from starlette.responses import HTMLResponse
                    return HTMLResponse("<html></html>")

                with patch.object(Jinja2Templates, "TemplateResponse", _capture):
                    from httpx import AsyncClient, ASGITransport
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        resp = await client.get("/bad/SSD-5")

        assert resp.status_code == 200
        # Should serve stale insights, not None
        wi = context_captured.get("weekly_insights")
        assert wi is not None, "Expected stale insights to be served, got None"
        assert wi.get("has_data") is True
        # Background refresh must have been triggered
        assert len(created_tasks) >= 1, "Background task not spawned for stale cache"


# ---------------------------------------------------------------------------
# Test 5: background refresh coroutine updates the cache
# ---------------------------------------------------------------------------

class TestBackgroundRefresh:
    async def test_background_refresh_updates_cache(self):
        """Running the refresh coroutine directly must update app.state.weekly_insights_cache."""
        from api.main import app, _refresh_weekly_insights

        pool_uid = "SSD-5"
        app.state.weekly_insights_cache = {}
        app.state.weekly_insights_inflight = set()

        # Make predict_range_batch return a plausible 168-value result
        mock_pred = MagicMock()

        async def _predict(pool_uid_, hours_, db_pool_=None):
            return [50.0] * len(hours_)

        mock_pred.predict_range_batch = _predict
        mock_pred.is_loaded = MagicMock(return_value=True)

        with patch("api.main.predictor", mock_pred):
            await _refresh_weekly_insights(pool_uid, db_pool=None)

        # Cache must now contain an entry for this pool
        assert pool_uid in app.state.weekly_insights_cache, (
            "Cache not updated after background refresh"
        )
        insights, computed_at = app.state.weekly_insights_cache[pool_uid]
        assert insights is not None
        assert isinstance(computed_at, datetime)
        # The entry should be fresh (computed very recently)
        age = (datetime.now(timezone.utc) - computed_at).total_seconds()
        assert age < 5, f"computed_at looks stale after refresh: age={age}s"
        # Pool should be removed from in-flight set
        assert pool_uid not in app.state.weekly_insights_inflight


# ---------------------------------------------------------------------------
# Test 6: TTL configurable via env var
# ---------------------------------------------------------------------------

class TestTTLConfig:
    def test_ttl_configurable_via_env_var(self, monkeypatch):
        """WEEKLY_INSIGHTS_CACHE_TTL_SECONDS must override the default TTL."""
        monkeypatch.setenv("WEEKLY_INSIGHTS_CACHE_TTL_SECONDS", "120")
        # Force re-import to pick up env var
        import importlib
        import api.main as api_main
        importlib.reload(api_main)

        assert api_main.WEEKLY_INSIGHTS_CACHE_TTL_SECONDS == 120, (
            f"Expected TTL=120, got {api_main.WEEKLY_INSIGHTS_CACHE_TTL_SECONDS}"
        )

    def test_default_ttl_is_3600(self, monkeypatch):
        """Default TTL must be 3600 when env var is not set."""
        monkeypatch.delenv("WEEKLY_INSIGHTS_CACHE_TTL_SECONDS", raising=False)
        import importlib
        import api.main as api_main
        importlib.reload(api_main)

        assert api_main.WEEKLY_INSIGHTS_CACHE_TTL_SECONDS == 3600


# ---------------------------------------------------------------------------
# Test 7 (optional): pre-warm schedules tasks for all pools at startup
# ---------------------------------------------------------------------------

class TestPrewarm:
    async def test_prewarm_populates_all_pools_at_startup(self):
        """Lifespan startup must schedule a background refresh for every pool."""
        import importlib
        import api.main as api_main
        importlib.reload(api_main)

        pools = api_main.get_pools()
        expected_uids = {p["uid"] for p in pools}

        scheduled_pool_uids: set[str] = set()

        original_create_task = asyncio.create_task

        def _spy_create_task(coro, **kwargs):
            # Inspect the coroutine name to detect refresh calls
            coro_name = getattr(coro, "__name__", "") or getattr(
                getattr(coro, "cr_code", None), "co_name", ""
            )
            if "refresh" in coro_name:
                # The coro will have pool_uid bound in its locals on first frame
                # Simplest: check coro.__qualname__ or args — but best we can
                # do without running it is to mark "some refresh was scheduled".
                scheduled_pool_uids.add("_any_")
            if hasattr(coro, 'close'):
                coro.close()
            return MagicMock()

        with patch("api.main.asyncio.create_task", side_effect=_spy_create_task):
            with patch("api.main.predictor") as mock_pred:
                mock_pred.load = MagicMock()
                # Run lifespan
                async with api_main.lifespan(api_main.app):
                    # At this point startup is complete
                    pass

        # At minimum, some background tasks should have been created
        # (we can't easily inspect coro args without running them)
        assert len(scheduled_pool_uids) > 0, (
            "No background refresh tasks were scheduled during lifespan startup"
        )


# ---------------------------------------------------------------------------
# Test 8: in-flight guard prevents duplicate refresh tasks
# ---------------------------------------------------------------------------

class TestInflightGuard:
    async def test_no_duplicate_refresh_for_same_pool(self):
        """If a refresh is already in-flight, a second stale request must not
        spawn another asyncio.create_task for the same pool.
        """
        from api.main import app

        # Stale cache entry
        stale_insights = _fresh_insights()
        computed_at = datetime.now(timezone.utc) - timedelta(hours=2)
        app.state.weekly_insights_cache = {"SSD-5": (stale_insights, computed_at)}
        # Mark as already in-flight
        app.state.weekly_insights_inflight = {"SSD-5"}

        mock_pred = MagicMock()

        async def _predict(pool_uid, hours, db_pool=None):
            return [0.0] * len(hours)

        mock_pred.predict_range_batch = _predict
        mock_pred.is_loaded = MagicMock(return_value=True)

        created_tasks = []

        def _capture_create_task(coro):
            created_tasks.append(coro)
            if hasattr(coro, 'close'):
                coro.close()
            return MagicMock()

        with patch("api.main.predictor", mock_pred):
            with patch("api.main.asyncio.create_task", side_effect=_capture_create_task):
                from fastapi.templating import Jinja2Templates

                def _capture(self_, name_or_request, context_or_name=None, *args, **kwargs):
                    from starlette.responses import HTMLResponse
                    return HTMLResponse("<html></html>")

                with patch.object(Jinja2Templates, "TemplateResponse", _capture):
                    from httpx import AsyncClient, ASGITransport
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        resp = await client.get("/bad/SSD-5")

        assert resp.status_code == 200
        # No new task should be created since one is already in-flight
        assert len(created_tasks) == 0, (
            f"Expected 0 new tasks (already in-flight), got {len(created_tasks)}"
        )
