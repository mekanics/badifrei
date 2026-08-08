"""JSON read APIs: occupancy, pools, predictions, history."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from api.catalog import ZURICH_TZ, get_pools
from api.dependencies import DbPool
from api.prediction_days import _classify_prediction_day, date_parser
from api.predictor import predictor
from api.schemas import (
    PoolInfo,
    PredictionResponse,
    RangePredictionItem,
    RangePredictionResponse,
)
from api.snapshots import load_current_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/current", tags=["dashboard"])
async def current_occupancy(db_pool: DbPool):
    """Latest occupancy + open status per known pool.

    Always one row per metadata pool. Occupancy fields are null when there is
    no ``pool_occupancy`` row (common for Hallenbäder during Revision).
    Returns [] only when the DB pool is unavailable.
    """
    return await load_current_snapshot(db_pool)


@router.get("/pools", response_model=list[PoolInfo], tags=["pools"])
async def list_pools():
    """List all monitored pools with metadata."""
    return get_pools()


@router.get("/predict", response_model=PredictionResponse, tags=["predictions"])
async def predict(db_pool: DbPool, pool_uid: str, dt_str: str):
    """Predict occupancy for a pool at a specific datetime (ISO 8601)."""
    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")

    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid datetime. Use ISO 8601 format (e.g. 2026-03-07T14:00:00).",
        )

    if not predictor.is_loaded():
        return PredictionResponse(
            pool_uid=pool_uid,
            pool_name=pool["name"],
            predicted_at=dt,
            predicted_occupancy_pct=0.0,
            model_version="no-model",
        )

    pcts = await predictor.predict_range_batch(pool_uid, [dt], db_pool)
    pct = pcts[0] if pcts else 0.0

    return PredictionResponse(
        pool_uid=pool_uid,
        pool_name=pool["name"],
        predicted_at=dt,
        predicted_occupancy_pct=pct,
        model_version=predictor.model_version,
    )


@router.get(
    "/predict/range", response_model=RangePredictionResponse, tags=["predictions"]
)
async def predict_range(db_pool: DbPool, pool_uid: str, date: str):
    """Predict hourly occupancy for a pool for an entire day."""
    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")

    try:
        d = date_parser(date)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid date: '{date}'")

    hours = [
        datetime(d.year, d.month, d.day, hour, 0, 0, tzinfo=ZURICH_TZ)
        for hour in range(24)
    ]

    model_available = predictor.is_loaded()
    status = _classify_prediction_day(pool, d, model_available)
    pct_values = await predictor.predict_range_batch(pool_uid, hours, db_pool)

    predictions = [
        RangePredictionItem(
            hour=dt.hour,
            predicted_at=dt,
            predicted_occupancy_pct=pct,
        )
        for dt, pct in zip(hours, pct_values)
    ]

    return RangePredictionResponse(
        pool_uid=pool_uid,
        pool_name=pool["name"],
        date=date,
        model_available=status["model_available"],
        model_version=predictor.model_version if model_available else "no-model",
        prediction_status=status["prediction_status"],
        open_hours_count=status["open_hours_count"],
        predictions=predictions,
    )


@router.get("/api/history", tags=["history"])
async def history(db_pool: DbPool, pool_uid: str, date: str):
    """Return hourly average occupancy from DB for a given pool and date."""
    from datetime import date as date_type

    null_actuals = [{"hour": i, "occupancy_pct": None} for i in range(24)]

    if not any(p["uid"] == pool_uid for p in get_pools()):
        raise HTTPException(status_code=404, detail="Pool not found")

    try:
        d = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Invalid date. Use YYYY-MM-DD format."
        )

    if db_pool is None:
        return {"pool_uid": pool_uid, "date": date, "actuals": null_actuals}

    try:
        rows = await db_pool.fetch(
            """
            SELECT
              EXTRACT(HOUR FROM time AT TIME ZONE 'Europe/Zurich') AS hour,
              AVG(occupancy_pct) AS occupancy_pct
            FROM pool_occupancy
            WHERE pool_uid = $1
              AND time >= $2
              AND time < $3
            GROUP BY hour
            ORDER BY hour
            """,
            pool_uid,
            d,
            d + timedelta(days=1),
        )
        hour_map = {
            int(row["hour"]): (
                float(row["occupancy_pct"])
                if row["occupancy_pct"] is not None
                else None
            )
            for row in rows
        }
        actuals = [{"hour": i, "occupancy_pct": hour_map.get(i)} for i in range(24)]
        return {"pool_uid": pool_uid, "date": date, "actuals": actuals}
    except Exception as e:
        logger.warning(f"History query failed: {e}")
        return {"pool_uid": pool_uid, "date": date, "actuals": null_actuals}
