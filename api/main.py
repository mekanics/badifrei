"""Badi Predictor API — FastAPI application."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

ZURICH_TZ = ZoneInfo("Europe/Zurich")

# ---------------------------------------------------------------------------
# Weekly insights cache configuration
# ---------------------------------------------------------------------------
WEEKLY_INSIGHTS_CACHE_TTL_SECONDS: int = int(
    os.environ.get("WEEKLY_INSIGHTS_CACHE_TTL_SECONDS", "3600")
)

UMAMI_SCRIPT_URL: str = os.environ.get("UMAMI_SCRIPT_URL", "")
UMAMI_WEBSITE_ID: str = os.environ.get("UMAMI_WEBSITE_ID", "")
_UMAMI_CSP_ORIGIN: str = ""
if UMAMI_SCRIPT_URL:
    _p = urlparse(UMAMI_SCRIPT_URL)
    _UMAMI_CSP_ORIGIN = f"{_p.scheme}://{_p.netloc}"

from dateutil.parser import parse as date_parser_raw  # noqa: E402

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import (  # noqa: E402
    JSONResponse as _JSONResponse,
    HTMLResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402


class JSONResponse(_JSONResponse):
    """Force charset=utf-8 in Content-Type to prevent Safari misinterpreting the encoding."""

    media_type = "application/json; charset=utf-8"


from api.schemas import (  # noqa: E402
    PoolInfo,
    PredictionResponse,
    RangePredictionResponse,
    RangePredictionItem,
)
from api.predictor import predictor  # noqa: E402

POOL_METADATA_PATH = Path(__file__).parent.parent / "ml" / "pool_metadata.json"
TEMPLATES_PATH = Path(__file__).parent / "templates"
STATIC_PATH = Path(__file__).parent / "static"

_pools_cache: list | None = None


def get_pools() -> list[dict]:
    global _pools_cache
    if _pools_cache is None:
        _pools_cache = json.loads(POOL_METADATA_PATH.read_text(encoding="utf-8"))
    return _pools_cache


# ---------------------------------------------------------------------------
# Weekly insights cache helpers
# ---------------------------------------------------------------------------


def is_stale(
    computed_at: datetime, ttl: int = WEEKLY_INSIGHTS_CACHE_TTL_SECONDS
) -> bool:
    """Return True if the cache entry is at or beyond its TTL."""
    age = (datetime.now(timezone.utc) - computed_at).total_seconds()
    return age >= ttl


async def _refresh_weekly_insights(pool_uid: str, db_pool) -> None:
    """Background coroutine: compute the 168-hour weekly insights and store in cache.

    Errors are logged at WARNING level; the existing cache entry is left intact
    so stale data is still served rather than nothing.
    """
    from fastapi import FastAPI  # avoid circular at module level

    app_state = app.state  # reference to running app state

    try:
        from datetime import date as date_type  # noqa: E402

        today = datetime.now(tz=ZURICH_TZ).date()
        mon = today - timedelta(days=today.weekday())
        flat_hours = [
            datetime(
                (mon + timedelta(days=d)).year,
                (mon + timedelta(days=d)).month,
                (mon + timedelta(days=d)).day,
                h,
                0,
                0,
                tzinfo=ZURICH_TZ,
            )
            for d in range(7)
            for h in range(24)
        ]

        flat_preds = await predictor.predict_range_batch(pool_uid, flat_hours, db_pool)
        weekly_preds = [flat_preds[d * 24 : (d + 1) * 24] for d in range(7)]
        insights = _compute_weekly_insights(weekly_preds)

        app_state.weekly_insights_cache[pool_uid] = (
            insights,
            datetime.now(timezone.utc),
        )
        logger.debug("Weekly insights cache refreshed for pool %s", pool_uid)
    except Exception as exc:
        logger.warning(
            "Failed to refresh weekly insights for pool %s: %s", pool_uid, exc
        )
    finally:
        # Always remove from in-flight guard so future requests can retry
        try:
            app_state.weekly_insights_inflight.discard(pool_uid)
        except Exception:
            pass


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

            app.state.db_pool = await asyncpg.create_pool(
                database_url, min_size=1, max_size=5
            )
            logger.info("DB connection pool created")
        except Exception as e:
            logger.warning(f"Could not create DB pool: {e}")

    # Initialise weekly insights cache
    app.state.weekly_insights_cache = {}  # pool_uid → (insights_dict, computed_at)
    app.state.weekly_insights_inflight = set()  # pool_uids currently being refreshed

    # Optional pre-warm: kick off a background refresh for every pool
    if predictor.is_loaded():
        for pool in get_pools():
            uid = pool["uid"]
            app.state.weekly_insights_inflight.add(uid)
            asyncio.create_task(_refresh_weekly_insights(uid, app.state.db_pool))
        logger.info("Weekly insights pre-warm scheduled for %d pools", len(get_pools()))

    yield

    if app.state.db_pool is not None:
        await app.state.db_pool.close()


templates = Jinja2Templates(directory=str(TEMPLATES_PATH))

# Cache-busting hash for static assets — recomputed on every deploy/restart
import hashlib as _hashlib  # noqa: E402

_css_path = STATIC_PATH / "style.css"
_STATIC_VER = (
    _hashlib.md5(_css_path.read_bytes()).hexdigest()[:8] if _css_path.exists() else "0"
)
templates.env.globals["static_ver"] = _STATIC_VER
templates.env.globals["umami_script_url"] = UMAMI_SCRIPT_URL
templates.env.globals["umami_website_id"] = UMAMI_WEBSITE_ID

_MONTHS_DE = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


def _fmt_date_de(value: str) -> str:
    """Format ISO date string (YYYY-MM-DD) as German date: '9. Mai 2026'."""
    try:
        parts = str(value).split("-")
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{d}. {_MONTHS_DE[m - 1]} {y}"
    except Exception:
        return str(value)


templates.env.filters["date_de"] = _fmt_date_de


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=()"
        )
        if "text/html" in response.headers.get("content-type", ""):
            _umami = f" {_UMAMI_CSP_ORIGIN}" if _UMAMI_CSP_ORIGIN else ""
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                f"script-src 'self' cdn.jsdelivr.net{_umami}; "
                "style-src 'self' fonts.googleapis.com 'unsafe-inline'; "
                "font-src 'self' fonts.gstatic.com; "
                "img-src 'self' data:; "
                f"connect-src 'self'{_umami};"
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

_cors_origins_raw = os.environ.get("CORS_ALLOWED_ORIGINS", "https://badifrei.ch")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)
app.add_middleware(SecurityHeadersMiddleware)


CITY_DISPLAY = {
    "zurich": "Zürich",
    "luzern": "Luzern",
    "bern": "Bern",
    "rotkreuz": "Rotkreuz",
    "adliswil": "Adliswil",
    "entfelden": "Entfelden",
    "hunenberg": "Hünenberg",
    "zug": "Zug",
    "wengen": "Wengen",
}
CITY_ORDER = [
    "zurich",
    "luzern",
    "bern",
    "adliswil",
    "rotkreuz",
    "entfelden",
    "hunenberg",
    "zug",
    "wengen",
]


@app.get("/", response_class=HTMLResponse, tags=["dashboard"])
async def dashboard_index(request: Request):
    """Pool overview dashboard."""
    pools = get_pools()
    # Group pools by city, Zürich first then alphabetical
    from collections import defaultdict  # noqa: E402

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


@app.get("/bad/{pool_uid}", response_class=HTMLResponse, tags=["pools"])
async def pool_detail(request: Request, pool_uid: str):
    """Pool detail dashboard."""
    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")

    # SSR today's predictions so the chart renders on first paint (SEO-001)
    now_zurich = datetime.now(tz=ZURICH_TZ)
    today = now_zurich.date()
    hours = [
        datetime(today.year, today.month, today.day, h, 0, 0, tzinfo=ZURICH_TZ)
        for h in range(24)
    ]
    db_pool = getattr(request.app.state, "db_pool", None)

    # Run today's predictions (24h) — the fast path, always computed fresh.
    async def _safe_predict(pool_uid_, hrs, db_pool_, fallback_len):
        try:
            return await predictor.predict_range_batch(pool_uid_, hrs, db_pool_)
        except Exception:
            return [0.0] * fallback_len

    today_predictions = await _safe_predict(pool_uid, hours, db_pool, 24)

    # Quietest open hour for FAQPage schema (SEO-008)
    open_preds = [(i, v) for i, v in enumerate(today_predictions) if v > 0]
    quietest_hour = min(open_preds, key=lambda x: x[1])[0] if open_preds else None

    opening_hours_summary = _build_opening_hours_summary(pool.get("opening_hours"))

    # Weekly "Beste Besuchszeiten" — served from in-memory cache (stale-while-revalidate).
    cache: dict = getattr(request.app.state, "weekly_insights_cache", {})
    inflight: set = getattr(request.app.state, "weekly_insights_inflight", set())

    cached_entry = cache.get(pool_uid)
    if cached_entry is not None:
        insights_dict, computed_at = cached_entry
        weekly_insights = insights_dict  # serve immediately (fresh or stale)
        if is_stale(computed_at) and pool_uid not in inflight:
            # Stale — kick off background refresh, serve old value now
            inflight.add(pool_uid)
            asyncio.create_task(_refresh_weekly_insights(pool_uid, db_pool))
    else:
        # Cold cache — return None immediately, schedule background computation
        weekly_insights = None
        if pool_uid not in inflight:
            inflight.add(pool_uid)
            asyncio.create_task(_refresh_weekly_insights(pool_uid, db_pool))

    # SEO-016: related pools in same city (same type first, then others; max 4)
    all_pools = get_pools()
    same_city = [
        p
        for p in all_pools
        if p.get("city") == pool.get("city") and p["uid"] != pool_uid
    ]
    same_type = [p for p in same_city if p.get("type") == pool.get("type")]
    other_type = [p for p in same_city if p.get("type") != pool.get("type")]
    related_pools = (same_type + other_type)[:4]

    return templates.TemplateResponse(
        request,
        "pool.html",
        {
            "pool": pool,
            "today_predictions_json": json.dumps(today_predictions),
            "today_date": today.isoformat(),
            "quietest_hour": quietest_hour,
            "opening_hours_summary": opening_hours_summary,
            "weekly_insights": weekly_insights,
            "related_pools": related_pools,
        },
    )


_DAY_DE_FULL = {
    0: "Montag",
    1: "Dienstag",
    2: "Mittwoch",
    3: "Donnerstag",
    4: "Freitag",
    5: "Samstag",
    6: "Sonntag",
}
_DAY_DE_SHORT = {
    0: "Mo",
    1: "Di",
    2: "Mi",
    3: "Do",
    4: "Fr",
    5: "Sa",
    6: "So",
}


def _compute_weekly_insights(weekly_preds: list[list[float]]) -> dict | None:
    """Derive best/worst visiting time insights from a 7×24 prediction grid.

    Args:
        weekly_preds: list of 7 lists, each with 24 floats (0.0 = closed).
                      Index 0 = Monday, 6 = Sunday.

    Returns dict with keys:
        quietest_day_name: str (e.g. "Dienstag")
        quietest_hour: int (0–23)
        quietest_hour_str: str (e.g. "09:00")
        peak_hour: int
        peak_hour_str: str
        weekday_quieter_than_weekend: bool
        has_data: bool  (False if all predictions are 0)
    """
    # Flatten to get all non-zero values
    all_open = [
        (day, hour, v)
        for day, hours in enumerate(weekly_preds)
        for hour, v in enumerate(hours)
        if v > 0
    ]

    if not all_open:
        return {"has_data": False}

    # Quietest single slot across the week
    quietest = min(all_open, key=lambda x: x[2])
    # Busiest single slot
    busiest = max(all_open, key=lambda x: x[2])

    # Average per day (only open hours)
    day_avgs = []
    for day_idx, day_hours in enumerate(weekly_preds):
        open_vals = [v for v in day_hours if v > 0]
        if open_vals:
            day_avgs.append((day_idx, sum(open_vals) / len(open_vals)))

    quietest_day_idx = min(day_avgs, key=lambda x: x[1])[0] if day_avgs else quietest[0]

    # Weekday vs weekend average
    weekday_vals = [v for day, h, v in all_open if day < 5]
    weekend_vals = [v for day, h, v in all_open if day >= 5]
    weekday_avg = sum(weekday_vals) / len(weekday_vals) if weekday_vals else None
    weekend_avg = sum(weekend_vals) / len(weekend_vals) if weekend_vals else None
    weekday_quieter = (
        (weekday_avg < weekend_avg) if (weekday_avg and weekend_avg) else None
    )

    return {
        "has_data": True,
        "quietest_day_name": _DAY_DE_FULL[quietest_day_idx],
        "quietest_hour": quietest[1],
        "quietest_hour_str": f"{quietest[1]:02d}:00",
        "peak_hour": busiest[1],
        "peak_hour_str": f"{busiest[1]:02d}:00",
        "weekday_quieter_than_weekend": weekday_quieter,
    }


def _build_opening_hours_summary(opening_hours: dict | None) -> str | None:
    """Build a compact German summary of opening hours, grouping days with identical times.

    Returns a string like "Mo–Fr: 09:00–20:00 Uhr. Sa–So: 10:00–18:00 Uhr."
    or None if no schedule data.
    """
    if not opening_hours:
        return None
    schedule = opening_hours.get("schedule")
    if not schedule:
        return None

    _DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _DAY_DE = {
        "Mon": "Mo",
        "Tue": "Di",
        "Wed": "Mi",
        "Thu": "Do",
        "Fri": "Fr",
        "Sat": "Sa",
        "Sun": "So",
    }

    # Build list of (day_key, open, close) for days that are open
    entries = []
    for day in _DAY_ORDER:
        s = schedule.get(day)
        if s:
            entries.append((day, s["open"], s["close"]))

    if not entries:
        return None

    # Group consecutive days with identical hours
    groups = []
    i = 0
    while i < len(entries):
        day, open_t, close_t = entries[i]
        # Find run of same hours
        j = i + 1
        while j < len(entries) and entries[j][1] == open_t and entries[j][2] == close_t:
            # Only group if consecutive in _DAY_ORDER
            idx_prev = _DAY_ORDER.index(entries[j - 1][0])
            idx_curr = _DAY_ORDER.index(entries[j][0])
            if idx_curr == idx_prev + 1:
                j += 1
            else:
                break
        # entries[i..j-1] are a group
        start_day = _DAY_DE[entries[i][0]]
        end_day = _DAY_DE[entries[j - 1][0]]
        if start_day == end_day:
            label = start_day
        else:
            label = f"{start_day}–{end_day}"
        groups.append(f"{label}: {open_t}–{close_t} Uhr")
        i = j

    return ". ".join(groups) + "."


_DE_MONTHS = [
    "Jan",
    "Feb",
    "Mär",
    "Apr",
    "Mai",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dez",
]
_DE_DAYS_SHORT = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]


def _compute_pool_is_open(pool: dict, now_zurich: "datetime") -> dict:
    """Compute is_open status for a pool given current Zürich time.

    Returns dict with keys:
      is_open (bool), next_open (str|None), opens_seasonal (str|None)

    opens_seasonal is set (e.g. "ab 9. Mai") when the pool is outside its
    seasonal window — next_open is None in that case.
    next_open is a time string (e.g. "09:00") for in-season daily closures.
    """
    import datetime as dt
    from ml.features import _DAY_NAMES

    opening_hours = pool.get("opening_hours")
    if not opening_hours:
        return {"is_open": True, "next_open": None, "opens_seasonal": None}

    today = now_zurich.date()

    # ── Seasonal window check (before daily schedule) ───────────────────────
    seasonal_open_str = opening_hours.get("seasonal_open")
    seasonal_close_str = opening_hours.get("seasonal_close")
    if seasonal_open_str and seasonal_close_str:
        try:
            season_open = dt.date.fromisoformat(seasonal_open_str)
            season_close = dt.date.fromisoformat(seasonal_close_str)
            if not (season_open <= today <= season_close):
                # Off-season — show the opening date, not a daily time
                label = f"ab {season_open.day}. {_DE_MONTHS[season_open.month - 1]}"
                return {"is_open": False, "next_open": None, "opens_seasonal": label}
        except (ValueError, TypeError):
            pass  # malformed date — fall through to daily schedule

    # ── In-season: check daily opening hours (minute-accurate) ──────────────
    day_of_week = now_zurich.weekday()  # 0=Mon
    hour = now_zurich.hour
    day_name = _DAY_NAMES[day_of_week]
    schedule = opening_hours.get("schedule", {})
    day_sched = schedule.get(day_name)

    is_open = False
    if day_sched:
        try:
            open_h, open_m = map(int, day_sched["open"].split(":"))
            close_h, close_m = map(int, day_sched["close"].split(":"))
            open_minutes = open_h * 60 + open_m
            close_minutes = close_h * 60 + close_m
            current_minutes = now_zurich.hour * 60 + now_zurich.minute
            is_open = open_minutes <= current_minutes < close_minutes
        except (KeyError, ValueError):
            is_open = True  # defensive: treat as open on parse error

    next_open = None
    if not is_open:

        # Check if still today and opens later (offset=0)
        today_sched = schedule.get(_DAY_NAMES[day_of_week])
        if today_sched:
            open_h, open_m = map(int, today_sched["open"].split(":"))
            if hour < open_h or (hour == open_h and now_zurich.minute < open_m):
                next_open = today_sched["open"]  # same day → time only

        # Find next open day ahead
        if not next_open:
            for offset in range(1, 8):
                check_dow = (day_of_week + offset) % 7
                day_sched = schedule.get(_DAY_NAMES[check_dow])
                if day_sched:
                    t = day_sched.get("open", "")
                    if offset == 1:
                        next_open = t  # tomorrow → time only
                    else:
                        next_open = (
                            f"{_DE_DAYS_SHORT[check_dow]} {t}"  # e.g. "So. 09:00"
                        )
                    break

    return {"is_open": bool(is_open), "next_open": next_open, "opens_seasonal": None}


@app.get("/api/current", tags=["dashboard"])
async def current_occupancy(request: Request):
    """Return latest occupancy reading per pool. Returns [] if DB unavailable."""
    now_zurich = datetime.now(ZURICH_TZ)

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
            item["opens_seasonal"] = status["opens_seasonal"]
            result.append(item)
        return result
    except Exception:
        return []


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/llms.txt", include_in_schema=False)
async def llms_txt():
    llms_path = STATIC_PATH / "llms.txt"
    content = llms_path.read_text(encoding="utf-8")
    return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    content = """# Content Signals Policy (https://contentsignals.org)
# search:   building a search index and returning results
# ai-input: using content as live input for AI-generated answers (RAG, grounding)
# ai-train: training or fine-tuning AI models

User-agent: *
Content-Signal: search=yes,ai-input=yes,ai-train=no
Allow: /
Disallow: /dashboard/
Disallow: /api/
Disallow: /predict/
Disallow: /health
Disallow: /pools

# Block AI training crawlers (answer-generation bots are allowed above)
User-agent: Amazonbot
Disallow: /

User-agent: Applebot-Extended
Disallow: /

User-agent: Bytespider
Disallow: /

User-agent: CCBot
Disallow: /

User-agent: ClaudeBot
Disallow: /

User-agent: Google-Extended
Disallow: /

User-agent: GPTBot
Disallow: /

User-agent: meta-externalagent
Disallow: /

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
async def predict(request: Request, pool_uid: str, dt_str: str):
    """Predict occupancy for a pool at a specific datetime (ISO 8601)."""
    # Validate pool exists
    pools = get_pools()
    pool = next((p for p in pools if p["uid"] == pool_uid), None)
    if pool is None:
        raise HTTPException(status_code=404, detail=f"Pool '{pool_uid}' not found")

    # Parse datetime
    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid datetime. Use ISO 8601 format (e.g. 2026-03-07T14:00:00).",
        )

    if not predictor.is_loaded():
        # Return a placeholder when model isn't trained yet
        return PredictionResponse(
            pool_uid=pool_uid,
            pool_name=pool["name"],
            predicted_at=dt,
            predicted_occupancy_pct=0.0,
            model_version="no-model",
        )

    # Delegate to the async batch path so live weather is fetched via fetch_weather_batch.
    # predict() is sync and cannot await weather; predict_range_batch handles it correctly.
    db_pool = getattr(request.app.state, "db_pool", None)
    pcts = await predictor.predict_range_batch(pool_uid, [dt], db_pool)
    pct = pcts[0] if pcts else 0.0

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

    db_pool = getattr(request.app.state, "db_pool", None)
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
        predictions=predictions,
    )


@app.get("/api/history", tags=["history"])
async def history(request: Request, pool_uid: str, date: str):
    """Return hourly average occupancy from DB for a given pool and date."""
    from datetime import date as date_type, timedelta

    null_actuals = [{"hour": i, "occupancy_pct": None} for i in range(24)]

    # Validate pool exists
    if not any(p["uid"] == pool_uid for p in get_pools()):
        raise HTTPException(status_code=404, detail="Pool not found")

    # Validate date early
    try:
        d = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Invalid date. Use YYYY-MM-DD format."
        )

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
