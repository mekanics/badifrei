"""Badi Predictor API — FastAPI application."""
import json
import logging
import os
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from pathlib import Path

from dateutil.parser import parse as datetime_parser
from dateutil.parser import parse as date_parser_raw

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse as _JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


class JSONResponse(_JSONResponse):
    """Force charset=utf-8 in Content-Type to prevent Safari misinterpreting the encoding."""
    media_type = "application/json; charset=utf-8"

from api.schemas import PoolInfo, PredictionResponse, RangePredictionResponse, RangePredictionItem
from api.predictor import predictor

POOL_METADATA_PATH = Path(__file__).parent.parent / "ml" / "pool_metadata.json"
TEMPLATES_PATH = Path(__file__).parent / "templates"
STATIC_PATH = Path(__file__).parent / "static"

_pools_cache: list | None = None


def get_pools() -> list[dict]:
    global _pools_cache
    if _pools_cache is None:
        _pools_cache = json.loads(POOL_METADATA_PATH.read_text(encoding="utf-8"))
    return _pools_cache


def date_parser(date_str: str):
    return date_parser_raw(date_str).date()


@asynccontextmanager
async def lifespan(app: FastAPI):
    predictor.load()

    # Create asyncpg connection pool for DB-backed endpoints
    database_url = os.environ.get("DATABASE_URL")
    app.state.db_pool = None
    if database_url:
        try:
            import asyncpg
            app.state.db_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
            logger.info("DB connection pool created")
        except Exception as e:
            logger.warning(f"Could not create DB pool: {e}")

    yield

    if app.state.db_pool is not None:
        await app.state.db_pool.close()


templates = Jinja2Templates(directory=str(TEMPLATES_PATH))

app = FastAPI(
    title="Badi Predictor",
    description="Predict pool occupancy for Zürich's public pools.",
    version="0.1.0",
    default_response_class=JSONResponse,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse, tags=["dashboard"])
async def dashboard_index(request: Request):
    """Pool overview dashboard."""
    pools = get_pools()
    return templates.TemplateResponse("index.html", {"request": request, "pools": pools})


@app.get("/dashboard/pools/{pool_uid}", response_class=HTMLResponse, tags=["dashboard"])
async def dashboard_pool(request: Request, pool_uid: str):
    """Pool detail dashboard."""
    from fastapi import HTTPException
    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")
    return templates.TemplateResponse("pool.html", {"request": request, "pool": pool})


@app.get("/api/current", tags=["dashboard"])
async def current_occupancy(request: Request):
    """Return latest occupancy reading per pool. Returns [] if DB unavailable."""
    db_pool = getattr(request.app.state, "db_pool", None)
    if db_pool is None:
        return []
    try:
        rows = await db_pool.fetch(
            """
            SELECT DISTINCT ON (pool_uid)
                pool_uid, current_fill, max_space, free_space,
                ROUND((current_fill::numeric / NULLIF(max_space, 0)) * 100, 1) AS occupancy_pct,
                time
            FROM pool_occupancy
            ORDER BY pool_uid, time DESC
            """
        )
        return [dict(row) for row in rows]
    except Exception:
        return []


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
