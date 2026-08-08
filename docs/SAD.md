# Software Architecture Document

**Project**: badifrei.ch (`badi-predictor`)
**Last Updated**: 2026-08-08
**Version**: 1.0

## Overview

badifrei.ch is a public read-only service that shows live pool occupancy and ML
forecasts for Swiss baths (primarily Zürich, with additional cities). A
WebSocket collector writes CrowdMonitor readings into TimescaleDB; an XGBoost
pipeline trains on that history; a FastAPI app serves a Jinja2 dashboard, JSON
prediction APIs, and markdown/LLM discovery surfaces.

## Architecture Style

Multi-service Docker Compose stack: long-running collector, API, and retrain
workers share one TimescaleDB instance and a named volume for model artifacts.
Not a distributed microservices platform — services are process boundaries for
lifecycle isolation, not independent deployable products.

## System Components

```
┌─────────────────┐     WebSocket      ┌──────────────────────┐
│  CrowdMonitor   │ ─────────────────► │  collector           │
│  (badi-info.ch) │                    │  (+ Baditicker poll) │
└─────────────────┘                    └──────────┬───────────┘
                                                  │
                                            TimescaleDB
                                                  │
                    ┌─────────────────────────────┼──────────────────┐
                    │                             │                  │
              ┌─────▼─────┐               ┌───────▼──────┐   ┌───────▼──────┐
              │  api      │◄── models ─── │  retrain     │   │  migrator    │
              │  FastAPI  │   (volume)    │  APScheduler │   │  (one-shot)  │
              └─────┬─────┘               └──────────────┘   └──────────────┘
                    │
              ┌─────▼─────┐
              │  Browser  │
              │  / agents │
              └───────────┘
```

## Technology Stack

| Layer        | Technology                     | Rationale                                                                                                       |
| ------------ | ------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| Frontend     | Jinja2 + vanilla JS + Chart.js | SSR dashboard; no SPA framework                                                                                 |
| Backend      | FastAPI + asyncpg              | Async I/O; simple JSON + HTML routes; layout and DB access per [ADR-002](./adr/ADR-002-fastapi-flat-asyncpg.md) |
| Collector    | Python asyncio + websockets    | Push-based CrowdMonitor feed                                                                                    |
| Database     | TimescaleDB (PostgreSQL 16)    | Time-series hypertables; SQL familiarity                                                                        |
| ML           | XGBoost + pandas               | Tabular occupancy regression                                                                                    |
| Weather      | Open-Meteo                     | Free forecast API; no key                                                                                       |
| Auth         | None                           | Public read-only product                                                                                        |
| Hosting      | Docker Compose / Coolify       | See [COOLIFY.md](./COOLIFY.md)                                                                                  |
| Package mgmt | `uv`                           | Project standard (see root `AGENTS.md`)                                                                         |

## Key Components

### collector

- **Responsibility**: Maintain CrowdMonitor WebSocket, dedupe occupancy writes,
  poll Stadt Zürich Baditicker for live open/closed observations, expose
  `/health` metrics.
- **Interfaces**: Writes `pool_occupancy` and `pool_status`; health on `:8080`.
- **Data ownership**: Ingest path only — does not serve user traffic.

### api

- **Responsibility**: SSR dashboard (`/`, `/bad/{pool_uid}`), JSON APIs
  (`/api/current`, `/pools`, `/predict`, `/predict/range`, `/api/history`),
  SEO/LLM surfaces (`/llms.txt`, `/index.md`, `/bad/{uid}.md`, sitemap,
  robots), structured data and hours Resolution via `ml/opening_hours.py`.
- **Interfaces**: Reads TimescaleDB pool; loads XGBoost artifact from
  `ml/models` (shared volume); optional Umami snippet via env.
- **Data ownership**: Request/response shaping and presentation; pool catalog
  from `ml/pool_metadata.json`.
- **Layout**: Flat FastAPI modules (`config`, `dependencies`, `routers/*`) with
  lifespan-scoped asyncpg pool via `Depends` — see
  [ADR-002](./adr/ADR-002-fastapi-flat-asyncpg.md).

### ml / retrain

- **Responsibility**: Feature engineering, training, evaluation, scheduled
  retrain (`ml/retrain.py`), lag/target policies, weather fetch/cache,
  Schedule domain (`ml/opening_hours.py`).
- **Interfaces**: Reads occupancy + weather tables; writes model artifacts
  (`model_latest.ubj`, reports) to shared volume consumed by `api`.
- **Data ownership**: Model files and training reports under `ml/models/`.

### migrator

- **Responsibility**: Apply numbered SQL under `docker/migrations/` via
  `scripts/migrate.py` before app services start (Compose) or as Coolify
  pre-deploy.
- **Interfaces**: One-shot container; tracks applied files in
  `schema_migrations`.
- **Data ownership**: Schema evolution only.

### TimescaleDB

- **Responsibility**: Persist occupancy, hourly weather, and pool status
  observations.
- **Primary tables**: `pool_occupancy` (hypertable), `hourly_weather`,
  `pool_status` — see migrations `001`–`003`.
- **Data ownership**: System of record for live and historical sensor data.
  Published hours live in git (`pool_metadata.json` / generated Schedule), not
  in the DB.

## Data Flow

1. **Ingest** — CrowdMonitor pushes pool fill updates; collector writes changed
   rows (plus periodic force-write). Baditicker XML is polled into
   `pool_status`.
2. **Weather** — Open-Meteo forecasts are cached in `hourly_weather` for
   training and inference features.
3. **Train** — Retrain job loads lookback window, builds features, trains
   XGBoost on clipped `occupancy_pct`, writes artifact + report.
4. **Serve** — API merges latest occupancy, Schedule Resolution (observation /
   closure / schedule), and predictions for HTML and JSON. Markdown and
   `llms.txt` surfaces summarize the same catalog for agents.

Domain terms (Schedule, Guaranteed hours, Resolution, Observation) are defined
in [glossary.md](./glossary.md).

## External Integrations

| Service      | Purpose                             | Authentication       |
| ------------ | ----------------------------------- | -------------------- |
| CrowdMonitor | Live occupancy WebSocket            | Public WS            |
| Baditicker   | Stadt Zürich open/closed XML        | Public HTTP          |
| Open-Meteo   | Hourly weather features             | None (no API key)    |
| Umami        | Optional privacy-friendly analytics | Script URL + site ID |

## Security Model

- **Authentication**: None — public read-only site and APIs.
- **Authorization**: N/A; no admin surface in production paths.
- **Secrets management**: `DATABASE_URL` and Compose Postgres vars via `.env`
  (gitignored); `.env.example` documents placeholders only.
- **Baseline review**: [SECURITY_REVIEW.md](./SECURITY_REVIEW.md) — consult
  before changing headers, CORS, Dockerfiles, analytics, or dependency loading.

## Scalability

- **Current capacity**: Single-node Compose; dozens of pools; dashboard refresh
  traffic and occasional agent crawls.
- **Scaling strategy**: Vertical for DB/API; collector is a single writer;
  retrain is periodic batch.
- **Known bottlenecks**: DB connection pooling and model load/reload behavior
  matter more than horizontal API replicas; shared `model_artifacts` volume
  couples api and retrain.

## ADR References

See [docs/adr/](./adr/) for architectural decision records.

- [ADR-001](./adr/ADR-001-guaranteed-hours-in-structured-data.md) — Guaranteed
  hours only in Hours JSON-LD
