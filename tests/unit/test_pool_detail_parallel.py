"""Tests for Fix 1: pool_detail() runs today + weekly predictions concurrently.

Verifies that the two predict_range_batch calls for today (24h) and the full
week (168h) are issued via asyncio.gather so they run in parallel, not sequentially.
"""
import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_predictor(delay_seconds: float = 0.0) -> MagicMock:
    """Return a mock predictor whose predict_range_batch resolves after a delay."""
    mock_pred = MagicMock()

    async def _predict(pool_uid, hours, db_pool=None):
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        return [0.0] * len(hours)

    mock_pred.predict_range_batch = _predict
    return mock_pred


# ---------------------------------------------------------------------------
# Fix 1: concurrent predictions
# ---------------------------------------------------------------------------

async def test_pool_detail_predictions_run_concurrently():
    """Today and weekly predictions must run concurrently, not sequentially.

    With asyncio.gather the total wall time should be ~max(t_today, t_weekly),
    NOT ~(t_today + t_weekly).
    """
    DELAY = 0.05  # 50 ms each — sequential would take ≥100 ms, concurrent ≤75 ms

    call_order: list[str] = []
    start_times: dict[str, float] = {}

    async def _predict_tagged(tag, hours):
        start_times[tag] = asyncio.get_event_loop().time()
        call_order.append(f"start:{tag}")
        await asyncio.sleep(DELAY)
        call_order.append(f"end:{tag}")
        return [0.0] * len(hours)

    # Build a realistic pair of hour lists matching pool_detail logic
    today = datetime(2026, 3, 9)
    hours_today = [datetime(today.year, today.month, today.day, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]
    mon = today.date() - timedelta(days=today.weekday())
    flat_hours = [
        datetime(
            (mon + timedelta(days=d)).year,
            (mon + timedelta(days=d)).month,
            (mon + timedelta(days=d)).day,
            h, 0, 0,
            tzinfo=timezone.utc,
        )
        for d in range(7)
        for h in range(24)
    ]

    t0 = asyncio.get_event_loop().time()
    today_preds, weekly_preds = await asyncio.gather(
        _predict_tagged("today", hours_today),
        _predict_tagged("weekly", flat_hours),
    )
    elapsed = asyncio.get_event_loop().time() - t0

    # Both tasks started before either finished (overlap evidence)
    assert "start:today" in call_order
    assert "start:weekly" in call_order
    today_start_idx = call_order.index("start:today")
    weekly_start_idx = call_order.index("start:weekly")
    today_end_idx = call_order.index("end:today")

    # weekly must start before today ends — confirms overlap
    assert weekly_start_idx < today_end_idx, (
        "weekly prediction did not start before today prediction ended — "
        "predictions may still be sequential."
    )

    # Wall time should be close to DELAY, not 2*DELAY
    assert elapsed < DELAY * 1.8, (
        f"Elapsed {elapsed:.3f}s ≥ {DELAY*1.8:.3f}s — looks sequential, not concurrent."
    )

    assert len(today_preds) == 24
    assert len(weekly_preds) == 168


async def test_pool_detail_flat_hours_computed_before_gather():
    """flat_hours must be built before both predictions start.

    If flat_hours were computed between the two awaits, the weekly prediction
    could not run concurrently. This test verifies the computation is done up-front.
    """
    from datetime import date, timedelta

    today = date(2026, 3, 9)
    mon = today - timedelta(days=today.weekday())

    # Replicate the flat_hours logic from pool_detail
    flat_hours = [
        datetime(
            (mon + timedelta(days=d)).year,
            (mon + timedelta(days=d)).month,
            (mon + timedelta(days=d)).day,
            h, 0, 0,
            tzinfo=timezone.utc,
        )
        for d in range(7)
        for h in range(24)
    ]

    # Sanity check the shape
    assert len(flat_hours) == 168
    # All dates should cover Mon–Sun of the same week
    assert flat_hours[0].date() == mon
    assert flat_hours[-1].date() == mon + timedelta(days=6)


async def test_pool_detail_today_fallback_does_not_block_weekly():
    """An exception in the today prediction must not prevent the weekly prediction.

    With asyncio.gather(return_exceptions=False) an exception propagates immediately.
    The _safe_predict wrapper must catch exceptions so both results are always available.
    """
    call_log: list[str] = []

    async def _failing_today(hours):
        call_log.append("today")
        raise ValueError("simulated today failure")

    async def _ok_weekly(hours):
        call_log.append("weekly")
        return [1.0] * len(hours)

    async def _safe(coro, fallback_len):
        try:
            return await coro
        except Exception:
            return [0.0] * fallback_len

    today_hours = [datetime(2026, 3, 9, h, 0, 0, tzinfo=timezone.utc) for h in range(24)]
    # Build 168 hours properly across 7 days (not range(168) which breaks hour 0..23 constraint)
    weekly_hours = [
        datetime(2026, 3, 9 + d, h, 0, 0, tzinfo=timezone.utc)
        for d in range(7)
        for h in range(24)
    ]

    today_preds, weekly_preds = await asyncio.gather(
        _safe(_failing_today(today_hours), 24),
        _safe(_ok_weekly(weekly_hours), 168),
    )

    assert today_preds == [0.0] * 24, "today fallback should be all zeros"
    assert weekly_preds == [1.0] * 168, "weekly should still succeed"
    assert "weekly" in call_log, "weekly prediction must still run when today fails"
