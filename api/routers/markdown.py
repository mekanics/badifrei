"""Markdown twins for AI agents."""

from datetime import datetime

from fastapi import APIRouter, HTTPException

from api.catalog import ZURICH_TZ, get_pools
from api.dependencies import DbPool
from api.markdown_surfaces import (
    HOME_MD_CACHE_MAX_AGE,
    POOL_MD_CACHE_MAX_AGE,
    markdown_response,
    render_home_markdown,
    render_pool_markdown,
)
from api.pool_page import _pool_hours_for_markdown
from api.prediction_days import _classify_prediction_day
from api.predictor import predictor
from api.snapshots import load_pool_snapshot

router = APIRouter()


@router.get("/index.md", include_in_schema=False)
async def homepage_markdown():
    """Curated Markdown index for AI agents (no live occupancy table)."""
    now_zurich = datetime.now(ZURICH_TZ)
    body = render_home_markdown(pools=get_pools(), as_of=now_zurich)
    return markdown_response(body, max_age=HOME_MD_CACHE_MAX_AGE)


@router.get("/bad/{pool_uid}.md", include_in_schema=False)
async def pool_detail_markdown(db_pool: DbPool, pool_uid: str):
    """Markdown twin: live occupancy + today's forecast for remaining open hours."""
    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")

    now_zurich = datetime.now(tz=ZURICH_TZ)
    today = now_zurich.date()
    hours = [
        datetime(today.year, today.month, today.day, h, 0, 0, tzinfo=ZURICH_TZ)
        for h in range(24)
    ]
    model_available = predictor.is_loaded()
    today_status = _classify_prediction_day(pool, today, model_available)

    async def _safe_predict(pool_uid_, hrs, db_pool_, fallback_len):
        try:
            return await predictor.predict_range_batch(pool_uid_, hrs, db_pool_)
        except Exception:
            return [0.0] * fallback_len

    today_predictions = await _safe_predict(pool_uid, hours, db_pool, 24)

    occupancy = await load_pool_snapshot(db_pool, pool)
    hours_summary, hours_detail = _pool_hours_for_markdown(pool, when=now_zurich)

    body = render_pool_markdown(
        pool=pool,
        occupancy=occupancy,
        today_predictions=today_predictions,
        prediction_status=today_status,
        as_of=now_zurich,
        now_zurich=now_zurich,
        opening_hours_summary=hours_summary,
        opening_hours_detail=hours_detail,
    )
    return markdown_response(body, max_age=POOL_MD_CACHE_MAX_AGE)
