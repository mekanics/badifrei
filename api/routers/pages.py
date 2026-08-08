"""Jinja HTML dashboard routes."""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from api.catalog import ZURICH_TZ, get_pools
from api.city_display import CITY_DISPLAY
from api.dependencies import DbPool
from api.pool_page import (
    _build_opening_hours_summary,
    _compute_related_pools,
    _detail_closed_notice,
    _related_pools_label,
)
from api.prediction_days import _classify_prediction_day, _schedule_for_pool
from api.predictor import predictor
from api.snapshots import (
    _fetch_city_weather_hints,
    _fetch_latest_observation,
    _latest_max_space,
)
from api.templating import templates
from api.weekly_insights import is_stale, refresh_weekly_insights

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse, tags=["dashboard"])
async def dashboard_index(request: Request):
    """Pool overview dashboard."""
    pools = get_pools()
    by_city: dict[str, list] = defaultdict(list)
    for p in pools:
        by_city[p.get("city", "zurich")].append(p)
    city_keys = sorted(by_city.keys(), key=lambda c: (c != "zurich", c))
    cities = [
        {"key": k, "label": CITY_DISPLAY.get(k, k.title()), "pools": by_city[k]}
        for k in city_keys
    ]
    return templates.TemplateResponse(
        request, "index.html", {"pools": pools, "cities": cities}
    )


@router.get("/bad/{pool_uid}", response_class=HTMLResponse, tags=["pools"])
async def pool_detail(request: Request, db_pool: DbPool, pool_uid: str):
    """Pool detail dashboard."""
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

    open_preds = [(i, v) for i, v in enumerate(today_predictions) if v > 0]
    quietest_hour = min(open_preds, key=lambda x: x[1])[0] if open_preds else None

    opening_hours_summary = _build_opening_hours_summary(pool.get("opening_hours"))

    cache: dict = getattr(request.app.state, "weekly_insights_cache", {})
    inflight: set = getattr(request.app.state, "weekly_insights_inflight", set())

    cached_entry = cache.get(pool_uid)
    if cached_entry is not None:
        insights_dict, computed_at = cached_entry
        weekly_insights = insights_dict
        if is_stale(computed_at) and pool_uid not in inflight:
            inflight.add(pool_uid)
            asyncio.create_task(
                refresh_weekly_insights(pool_uid, db_pool, request.app.state)
            )
    else:
        weekly_insights = None
        if pool_uid not in inflight:
            inflight.add(pool_uid)
            asyncio.create_task(
                refresh_weekly_insights(pool_uid, db_pool, request.app.state)
            )

    related_pools, related_pools_scope = _compute_related_pools(pool, pools, limit=8)
    related_pools_heading = _related_pools_label(related_pools_scope, pool)

    active_closure = None
    hours_confidence = "unverified"
    hours_scraped_at = None
    hours_view = None
    hours_jsonld: list = []
    hours_faq = opening_hours_summary
    try:
        from ml.opening_hours import (
            DE_MONTHS,
            hours_display_view,
            opening_hours_faq_text,
            opening_hours_jsonld,
            opening_hours_summary_from_schedule,
            resolve,
            use_observed_override,
        )

        schedule = _schedule_for_pool(pool)
        if schedule is not None:
            hours_confidence = schedule.confidence
            now_zurich = datetime.now(ZURICH_TZ)
            hours_jsonld = opening_hours_jsonld(schedule)
            hours_faq = opening_hours_faq_text(schedule, pool["name"], when=now_zurich)
            hours_view = hours_display_view(schedule, now_zurich.date())
            schedule_summary = opening_hours_summary_from_schedule(
                schedule, now_zurich.date()
            )
            if schedule_summary:
                opening_hours_summary = schedule_summary
            if schedule.scraped_at is not None:
                hours_scraped_at = (
                    f"{schedule.scraped_at.day}. "
                    f"{DE_MONTHS[schedule.scraped_at.month - 1]} "
                    f"{schedule.scraped_at.year}"
                )
            weather_hint = None
            observation = None
            if db_pool is not None:
                city = pool.get("city", "zurich")
                weather_by_city = await _fetch_city_weather_hints(
                    db_pool, now_zurich, cities={city}
                )
                weather_hint = weather_by_city.get(city)
                observation = await _fetch_latest_observation(db_pool, pool_uid)

            obs = observation if use_observed_override() else None
            resolution = resolve(
                schedule, now_zurich, observation=obs, weather=weather_hint
            )
            active_closure = _detail_closed_notice(schedule, resolution, now_zurich)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hours/closure block failed for %s: %s", pool_uid, exc)

    city_key = pool.get("city", "zurich")
    city_label = CITY_DISPLAY.get(city_key, city_key.title())
    schema_description = (
        f"{str(pool.get('type', 'Schwimmbad')).title()} in {city_label}"
        f" – aktuelle Auslastung und Tagesprognose auf badifrei.ch."
    )
    if hours_view is not None and hours_view.has_fair_weather:
        schema_description += (
            " Bei schönem Wetter teilweise verlängerte Öffnungszeiten "
            "(siehe Öffnungszeiten auf dieser Seite)."
        )

    live_max_space = await _latest_max_space(db_pool, pool_uid)

    return templates.TemplateResponse(
        request,
        "pool.html",
        {
            "pool": pool,
            "today_predictions_json": json.dumps(today_predictions),
            "today_date": today.isoformat(),
            "today_prediction_status": today_status["prediction_status"],
            "quietest_hour": quietest_hour,
            "opening_hours_summary": opening_hours_summary,
            "hours_jsonld": hours_jsonld,
            "hours_faq": hours_faq,
            "schema_description": schema_description,
            "weekly_insights": weekly_insights,
            "related_pools": related_pools,
            "related_pools_heading": related_pools_heading,
            "active_closure": active_closure,
            "hours_confidence": hours_confidence,
            "hours_scraped_at": hours_scraped_at,
            "hours_view": hours_view,
            "live_max_space": live_max_space,
        },
    )
