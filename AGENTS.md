# badi-predictor

`badi-predictor` powers badifrei.ch: live occupancy and ML forecasts for Zurich pools. It collects CrowdMonitor WebSocket data, stores it in TimescaleDB, trains XGBoost models, and serves a FastAPI/Jinja2 dashboard.

**Stack:** Python 3.12 + FastAPI + TimescaleDB + XGBoost · uv

## Build & Test

- Install: `uv sync --extra dev` (CI: `uv sync --frozen --extra dev`)
- Dev: `docker compose up -d` · API only: `uv run uvicorn api.main:app --reload`
- Test: `uv run pytest tests/unit -v` · all: `uv run pytest tests/ -v` · integration: `uv run pytest tests/integration -v -m integration`
- Lint/Format: `uv run ruff check .` · `uv run ruff format --check .` · format: `uv run ruff format . && uv run ruff check --fix .`
- Migrate: `uv run python scripts/migrate.py`
- Train: `uv run python scripts/train.py` · Docker: `docker compose run --rm retrain`

Package manager: `uv` only (not pip/poetry/conda). Prefer `uv run ...` over Makefile `.venv/bin/*` targets unless testing Make itself.

## House Rules

These rules apply to every task in this project unless explicitly overridden.
Bias: judgment over speed on non-trivial work.

---

## Rule 1 — Think, Then Investigate

State assumptions before writing code; ask rather than guess when real ambiguity exists.
Before fixing a bug, trace the data flow from source to symptom and form a hypothesis — no fix without investigation.
After three failed fixes, stop and re-investigate from scratch.

## Rule 2 — Read Before You Write

Read the existing exports, callers, and shared utilities before adding anything — reuse beats reinvention.
Read the whole function, not just the target line, before editing it.

## Rule 3 — Boil the Lake, Not the Ocean

AI-assisted time is cheap: when full test coverage, all edge cases, and every error path cost minutes more than the shortcut, do the complete thing.
Don't boil the ocean — no rewriting entire systems or speculative abstractions for single-use code.

## Rule 4 — Surgical Changes

Touch only what you must; clean up only your own mess.
Don't refactor or restyle adjacent code that isn't the cause of the problem.
Test: would a senior engineer say this touches too much?

## Rule 5 — Tests Encode Why

A feature isn't done until there's a regression test that fails without the change and passes with it.
A test that can't fail when business logic changes is wrong.

## Rule 6 — Match Conventions; Surface Conflicts

Conformance beats taste inside an established codebase — match existing naming, structure, and formatting.
If a convention is genuinely harmful, or two patterns contradict each other, say so explicitly and pick the more tested one; don't fork silently or blend them into a third thing nobody uses.

## Rule 7 — Fail Loud

"Completed" is wrong if anything was skipped silently; "tests pass" is wrong if any were skipped or disabled.
Default to surfacing uncertainty, not hiding it.

## Repository Map

- `api/`: FastAPI app, Jinja2 templates, static CSS/JS, prediction endpoints,
  detail-page weather temps (`water_temperature.py`, `weather_display.py`).
- `collector/`: WebSocket ingestion service and database writer.
- `ml/`: feature engineering, training, inference, weather integration, pool metadata.
- `scripts/`: migrations, training, SEO/analytics verification, walk-forward tooling.
- `docker/migrations/`: numbered SQL migrations applied by `scripts/migrate.py`.
- `tests/unit/`: unit tests that should not require a live database.
- `tests/integration/`: integration tests that may require TimescaleDB or Compose.
- `docs/PRD.md`: product scope (authoritative).
- `docs/SAD.md`: system architecture (authoritative).
- `docs/glossary.md`: ubiquitous language (Schedule / hours, water-temp freshness).
- `docs/adr/`: architectural decision records.
- `docs/COOLIFY.md`: deployment and migration behavior.
- `docs/SECURITY_REVIEW.md`: known security findings and remediation context.
- `docs/README.md`: documentation index.

## Development Rules

- Follow TDD for behavior changes: write or update tests first (Red → Green → Refactor).
- Keep API responses and frontend templates compatible with the current Jinja2 + vanilla JS setup; do not introduce a frontend framework unless explicitly requested.
- Use existing modules for shared behavior instead of duplicating logic across `api/`, `collector/`, and `ml/`.
- Keep migrations idempotent where possible and add new SQL files under `docker/migrations/` with the next numeric prefix.
- Treat `ml/pool_metadata.json` as domain data; preserve existing pool IDs and opening-hours semantics unless the task is explicitly about metadata.
- Do not commit `.env`, local database dumps, generated model artifacts, coverage output, or virtualenv files.

## Verification Expectations

- For Python logic changes, run the narrowest relevant unit tests with `uv run pytest ...`.
- For shared behavior, run `uv run pytest tests/unit -v`.
- For database or migration changes, run `uv run python scripts/migrate.py` against a test database and the relevant integration tests.
- Before handing off broad changes, run `uv run ruff check .` and `uv run pytest tests/unit -v`.

## Security And Deployment Notes

- This is a public read-only service; avoid adding authentication, tracking, or external data sharing without explicit approval.
- Use parameterized SQL through existing database helpers; never build SQL from unsanitized strings.
- Keep secrets in environment variables and `.env.example` only as documentation.
- Review `docs/SECURITY_REVIEW.md` before changing security headers, CORS, Dockerfiles, analytics, or dependency loading.
- Coolify deploys rely on `scripts/migrate.py`; keep migration behavior compatible with `docs/COOLIFY.md`.
