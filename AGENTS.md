# AGENTS.md

## Project Overview

`badi-predictor` powers badifrei.ch: live occupancy and ML forecasts for Zurich pools. It collects CrowdMonitor WebSocket data, stores it in TimescaleDB, trains XGBoost models, and serves a FastAPI/Jinja2 dashboard.

## Operating Rules

These rules apply to every task in this project unless explicitly overridden. Prefer caution over speed on non-trivial work, and use judgment on trivial tasks.

1. Think before coding. State assumptions explicitly. If uncertain, ask rather than guess. Present multiple interpretations when ambiguity exists, push back when a simpler approach exists, and stop when confused by naming what is unclear.
2. Simplicity first. Write the minimum code that solves the problem. Do not add speculative features or abstractions for single-use code. If a senior engineer would call it overcomplicated, simplify.
3. Make surgical changes. Touch only what is required, clean up only your own mess, and do not improve adjacent code, comments, or formatting. Do not refactor what is not broken; match existing style.
4. Execute toward success criteria. Define what success means, then iterate until it is verified. Do not blindly follow steps when the goal requires adapting.
5. Use the model only for judgment calls. Use model reasoning for classification, drafting, summarization, and extraction. Use code for routing, retries, deterministic transforms, and anything else code can answer.
6. Treat token budgets as hard limits. Per-task budget is 4,000 tokens; per-session budget is 30,000 tokens. If approaching the budget, summarize and start fresh. Surface the breach instead of silently overrunning.
7. Surface conflicts instead of averaging them. If two patterns contradict, pick one based on recency or evidence, explain why, and flag the other for cleanup.
8. Read before writing. Before adding code, read exports, immediate callers, and shared utilities. If you are unsure why code is structured a certain way, ask.
9. Make tests verify intent. Tests should encode why behavior matters, not only what it does. A test that cannot fail when business logic changes is the wrong test.
10. Checkpoint after significant steps. Summarize what changed, what is verified, and what remains. If you lose track, stop and restate the current state.
11. Match codebase conventions even when you disagree. Conformance beats personal taste inside this repository. If a convention seems harmful, surface it instead of silently forking it.
12. Fail loud. Do not claim completion if anything was skipped silently. Do not say tests pass if any relevant tests were skipped. Surface uncertainty by default.

## Runtime And Tooling

- Python version: 3.12+
- Package manager: `uv` only; do not use `pip`, `poetry`, or `conda` for project dependencies.
- Local dependency setup: `uv sync --extra dev`
- CI-style dependency setup: `uv sync --frozen --extra dev`
- Formatting/linting: Ruff and Black, both configured for line length 88.
- Local Docker stack: `docker compose up -d`

## Common Commands

- Run unit tests: `uv run pytest tests/unit -v`
- Run all tests: `uv run pytest tests/ -v`
- Run integration tests: `uv run pytest tests/integration -v -m integration`
- Run lint check: `uv run ruff check .`
- Run Black check: `uv run black --check .`
- Format code: `uv run black . && uv run ruff check --fix .`
- Run DB migrations locally: `uv run python scripts/migrate.py`
- Train model locally with default DB URL: `uv run python scripts/train.py`
- Train model through Docker: `docker compose run --rm retrain`
- Run API locally: `uv run uvicorn api.main:app --reload`

The `Makefile` wraps several commands but currently points directly at `.venv/bin/*`; prefer the `uv run ...` forms above unless you are deliberately testing the Make targets.

## Repository Map

- `api/`: FastAPI app, Jinja2 templates, static CSS/JS, prediction endpoints.
- `collector/`: WebSocket ingestion service and database writer.
- `ml/`: feature engineering, training, inference, weather integration, pool metadata.
- `scripts/`: migrations, training, SEO/analytics verification, walk-forward tooling.
- `docker/migrations/`: numbered SQL migrations applied by `scripts/migrate.py`.
- `tests/unit/`: unit tests that should not require a live database.
- `tests/integration/`: integration tests that may require TimescaleDB or Compose.
- `docs/PRD.md`: original product and system requirements.
- `docs/TASKS.md`: TDD-first task history and implementation notes.
- `docs/COOLIFY.md`: deployment and migration behavior.
- `docs/SECURITY_REVIEW.md`: known security findings and remediation context.

## Development Rules

- Follow the existing TDD convention from `docs/TASKS.md`: write or update tests for behavior changes.
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
