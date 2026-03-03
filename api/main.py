"""Badi Predictor API — FastAPI application."""
import json
import logging
import os
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from pathlib import Path

from dateutil.parser import parse as date_parser_raw

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse as _JSONResponse, HTMLResponse, PlainTextResponse, Response
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' fonts.googleapis.com 'unsafe-inline'; "
                "font-src 'self' fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self';"
            )
        return response


app = FastAPI(
    title="Badi Predictor",
    description="Predict pool occupancy for Zürich's public pools.",
    version="0.1.0",
    default_response_class=JSONResponse,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)
app.add_middleware(SecurityHeadersMiddleware)


CITY_DISPLAY = {
    "zurich": "Zürich",
    "luzern": "Luzern",
    "zug": "Zug",
    "wengen": "Wengen",
}
CITY_ORDER = ["zurich", "luzern", "zug", "wengen"]


@app.get("/", response_class=HTMLResponse, tags=["dashboard"])
async def dashboard_index(request: Request):
    """Pool overview dashboard."""
    pools = get_pools()
    # Group pools by city, Zürich first then alphabetical
    from collections import defaultdict
    by_city: dict[str, list] = defaultdict(list)
    for p in pools:
        by_city[p.get("city", "zurich")].append(p)
    city_keys = sorted(by_city.keys(), key=lambda c: (c != "zurich", c))
    cities = [
        {"key": k, "label": CITY_DISPLAY.get(k, k.title()), "pools": by_city[k]}
        for k in city_keys
    ]
    return templates.TemplateResponse("index.html", {"request": request, "pools": pools, "cities": cities})


@app.get("/bad/{pool_uid}", response_class=HTMLResponse, tags=["pools"])
async def pool_detail(request: Request, pool_uid: str):
    """Pool detail dashboard."""
    from fastapi import HTTPException
    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")
    return templates.TemplateResponse("pool.html", {"request": request, "pool": pool})


def _compute_pool_is_open(pool: dict, now_zurich: "datetime") -> dict:
    """Compute is_open status for a pool given current Zürich time.

    Returns dict with keys: is_open (bool), next_open (str|None).
    """
    from ml.features import compute_opening_hours_for_row, _DAY_NAMES
    opening_hours = pool.get("opening_hours")
    if not opening_hours:
        return {"is_open": True, "next_open": None}

    day_of_week = now_zurich.weekday()  # 0=Mon
    hour = now_zurich.hour
    is_open, _, _ = compute_opening_hours_for_row(hour, day_of_week, opening_hours)

    next_open = None
    if not is_open:
        # Find next opening time (look ahead up to 7 days)
        schedule = opening_hours.get("schedule", {})
        for offset in range(1, 8):
            check_dow = (day_of_week + offset) % 7
            day_name = _DAY_NAMES[check_dow]
            day_sched = schedule.get(day_name)
            if day_sched:
                next_open = day_sched.get("open")
                break
        # Also check if still today and opens later
        day_name = _DAY_NAMES[day_of_week]
        today_sched = schedule.get(day_name)
        if today_sched:
            open_h, open_m = map(int, today_sched["open"].split(":"))
            if hour < open_h or (hour == open_h and now_zurich.minute < open_m):
                next_open = today_sched["open"]

    return {"is_open": bool(is_open), "next_open": next_open}


@app.get("/api/current", tags=["dashboard"])
async def current_occupancy(request: Request):
    """Return latest occupancy reading per pool. Returns [] if DB unavailable."""
    import zoneinfo
    tz_zurich = zoneinfo.ZoneInfo("Europe/Zurich")
    now_zurich = datetime.now(tz_zurich)

    pools_by_uid = {p["uid"]: p for p in get_pools()}

    db_pool = getattr(request.app.state, "db_pool", None)
    if db_pool is None:
        return []
    try:
        rows = await db_pool.fetch(
            """
            SELECT DISTINCT ON (pool_uid)
                pool_uid, current_fill, max_space, free_space,
                ROUND((current_fill::numeric / NULLIF(max_space, 0)) * 100) AS occupancy_pct,
                time
            FROM pool_occupancy
            ORDER BY pool_uid, time DESC
            """
        )
        result = []
        for row in rows:
            item = dict(row)
            pool = pools_by_uid.get(item["pool_uid"], {})
            status = _compute_pool_is_open(pool, now_zurich)
            item["is_open"] = status["is_open"]
            item["next_open"] = status["next_open"]
            result.append(item)
        return result
    except Exception:
        return []


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    content = """User-agent: *
Allow: /
Disallow: /dashboard/
Disallow: /api/
Disallow: /predict/
Disallow: /health
Disallow: /pools
Sitemap: https://badifrei.ch/sitemap.xml
"""
    return PlainTextResponse(content)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    pool_uids = [p["uid"] for p in get_pools()]
    today = datetime.now(timezone.utc).date().isoformat()

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += (
        "  <url>\n"
        "    <loc>https://badifrei.ch/</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
    )
    for uid in pool_uids:
        xml += (
            "  <url>\n"
            f"    <loc>https://badifrei.ch/bad/{uid}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            "    <changefreq>always</changefreq>\n"
            "    <priority>0.8</priority>\n"
            "  </url>\n"
        )
    xml += "</urlset>"

    return Response(content=xml, media_type="application/xml")


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
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid datetime. Use ISO 8601 format (e.g. 2026-03-07T14:00:00).")

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
async def predict_range(request: Request, pool_uid: str, date: str):
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

    hours = [
        datetime(d.year, d.month, d.day, hour, 0, 0, tzinfo=timezone.utc)
        for hour in range(24)
    ]

    db_pool = getattr(request.app.state, "db_pool", None)
    pct_values = await predictor.predict_range_batch(pool_uid, hours, db_pool)

    predictions = [
        RangePredictionItem(
            hour=hour,
            predicted_at=hours[hour],
            predicted_occupancy_pct=pct_values[hour],
        )
        for hour in range(24)
    ]

    return RangePredictionResponse(
        pool_uid=pool_uid,
        pool_name=pool["name"],
        date=date,
        predictions=predictions,
    )


@app.get("/api/history", tags=["history"])
async def history(request: Request, pool_uid: str, date: str):
    """Return hourly average occupancy from DB for a given pool and date."""
    from datetime import date as date_type, timedelta
    from fastapi import HTTPException
    null_actuals = [{"hour": i, "occupancy_pct": None} for i in range(24)]

    # Validate pool exists
    if not any(p["uid"] == pool_uid for p in get_pools()):
        raise HTTPException(status_code=404, detail="Pool not found")

    # Validate date early
    try:
        d = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date. Use YYYY-MM-DD format.")

    db_pool = getattr(request.app.state, "db_pool", None)
    if db_pool is None:
        return {"pool_uid": pool_uid, "date": date, "actuals": null_actuals}

    try:
        # d already parsed above
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
        hour_map = {int(row["hour"]): float(row["occupancy_pct"]) if row["occupancy_pct"] is not None else None for row in rows}
        actuals = [{"hour": i, "occupancy_pct": hour_map.get(i)} for i in range(24)]
        return {"pool_uid": pool_uid, "date": date, "actuals": actuals}
    except Exception as e:
        logger.warning(f"History query failed: {e}")
        return {"pool_uid": pool_uid, "date": date, "actuals": null_actuals}
