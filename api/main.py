"""Badi Predictor API — FastAPI application."""
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dateutil.parser import parse as datetime_parser
from dateutil.parser import parse as date_parser_raw

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import PoolInfo, PredictionResponse, RangePredictionResponse, RangePredictionItem
from api.predictor import predictor

POOL_METADATA_PATH = Path(__file__).parent.parent / "ml" / "pool_metadata.json"

_pools_cache: list | None = None


def get_pools() -> list[dict]:
    global _pools_cache
    if _pools_cache is None:
        _pools_cache = json.load(open(POOL_METADATA_PATH))
    return _pools_cache


def date_parser(date_str: str):
    return date_parser_raw(date_str).date()


@asynccontextmanager
async def lifespan(app: FastAPI):
    predictor.load()
    yield


app = FastAPI(
    title="Badi Predictor",
    description="Predict pool occupancy for Zürich's public pools.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/pools", response_model=list[PoolInfo], tags=["pools"])
async def list_pools():
    """List all monitored pools with metadata."""
    return get_pools()


@app.get("/predict", response_model=PredictionResponse, tags=["predictions"])
async def predict(pool_uid: str, dt_str: str):
    """Predict occupancy for a pool at a specific datetime (ISO 8601)."""
    from fastapi import HTTPException

    # Validate pool exists
    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")

    # Parse datetime
    try:
        dt = datetime_parser(dt_str)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid datetime: '{dt_str}'")

    if not predictor.is_loaded():
        # Return a placeholder when model isn't trained yet
        return PredictionResponse(
            pool_uid=pool_uid,
            pool_name=pool["name"],
            predicted_at=dt,
            predicted_occupancy_pct=0.0,
            model_version="no-model",
        )

    pct = predictor.predict(pool_uid, dt)
    return PredictionResponse(
        pool_uid=pool_uid,
        pool_name=pool["name"],
        predicted_at=dt,
        predicted_occupancy_pct=pct,
        model_version=predictor.model_version,
    )


@app.get("/predict/range", response_model=RangePredictionResponse, tags=["predictions"])
async def predict_range(pool_uid: str, date: str):
    """Predict hourly occupancy for a pool for an entire day."""
    from fastapi import HTTPException

    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")

    try:
        d = date_parser(date)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid date: '{date}'")

    predictions = []
    for hour in range(24):
        dt = datetime(d.year, d.month, d.day, hour, 0, 0, tzinfo=timezone.utc)
        if predictor.is_loaded():
            pct = predictor.predict(pool_uid, dt)
        else:
            pct = 0.0
        predictions.append(RangePredictionItem(
            hour=hour,
            predicted_at=dt,
            predicted_occupancy_pct=pct,
        ))

    return RangePredictionResponse(
        pool_uid=pool_uid,
        pool_name=pool["name"],
        date=date,
        predictions=predictions,
    )
