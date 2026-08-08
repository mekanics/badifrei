"""Badi Predictor API — FastAPI application shell. See ADR-002."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse as _JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from api.catalog import get_pools
from api.config import get_settings
from api.predictor import predictor
from api.routers import json_api, markdown, meta, pages
from api.templating import STATIC_PATH, configure_templates
from api.weekly_insights import refresh_weekly_insights

logger = logging.getLogger(__name__)


class JSONResponse(_JSONResponse):
    """Force charset=utf-8 in Content-Type to prevent Safari misinterpreting the encoding."""

    media_type = "application/json; charset=utf-8"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_templates()
    predictor.load()

    app.state.db_pool = None
    if settings.database_url:
        try:
            import asyncpg

            app.state.db_pool = await asyncpg.create_pool(
                settings.database_url, min_size=1, max_size=5
            )
            logger.info("DB connection pool created")
        except Exception as e:
            logger.warning(f"Could not create DB pool: {e}")

    app.state.weekly_insights_cache = {}
    app.state.weekly_insights_inflight = set()

    if predictor.is_loaded():
        for pool in get_pools():
            uid = pool["uid"]
            app.state.weekly_insights_inflight.add(uid)
            asyncio.create_task(
                refresh_weekly_insights(uid, app.state.db_pool, app.state)
            )
        logger.info("Weekly insights pre-warm scheduled for %d pools", len(get_pools()))

    yield

    if app.state.db_pool is not None:
        await app.state.db_pool.close()


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
            csp_origin = get_settings().umami_csp_origin
            _umami = f" {csp_origin}" if csp_origin else ""
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                f"script-src 'self' cdn.jsdelivr.net{_umami}; "
                "style-src 'self' fonts.googleapis.com 'unsafe-inline'; "
                "font-src 'self' fonts.gstatic.com; "
                "img-src 'self' data:; "
                f"connect-src 'self'{_umami};"
            )
        return response


settings = get_settings()

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
    allow_origins=settings.cors_origins,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)
app.add_middleware(SecurityHeadersMiddleware)

# Markdown twins before HTML /bad/{uid} so "{uid}.md" is not captured as a pool uid.
app.include_router(markdown.router)
app.include_router(pages.router)
app.include_router(json_api.router)
app.include_router(meta.router)
