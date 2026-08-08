# ADR-002: Flat FastAPI layout with asyncpg pool injection

**Date**: 2026-08-08
**Status**: Accepted
**Deciders**: Eng review (api-structure)

## Context

The API lives mostly in a single ~1.6k-line `api/main.py`. Common FastAPI
"small app" guidance recommends `routers/`, `dependencies.py`,
`pydantic-settings`, and SQLModel (SQLAlchemy 2 + Pydantic) with
session-per-request `Depends()`.

This codebase already uses **asyncpg** for TimescaleDB across API, collector,
and ML loaders, with hand-written SQL suited to hypertables and
`DISTINCT ON` occupancy queries. Introducing SQLModel would be a second
persistence stack for little leverage on a read-heavy public API with
<20 endpoints.

We want a flatter, DI-friendly module layout without adopting Clean
Architecture or an ORM.

## Options Considered

### Option 1: Status quo (god `main.py`, `os.environ`, `request.app.state`)

Keep all routes and helpers in `api/main.py`; read `DATABASE_URL` via
`os.environ`; grab `db_pool` from `request.app.state`.

- Pros: No move risk; zero new dependencies
- Cons: Hard to navigate; weak typed config; DI anti-pattern; invites
  accidental behavior drift during unrelated edits

### Option 2: Flat FastAPI layout + asyncpg pool via Depends (chosen direction)

Extract `api/config.py` (pydantic-settings), `api/dependencies.py`
(`DbPool = Annotated[Pool | None, Depends(...)]`), `api/routers/*`, and a
small set of support modules. Keep lifespan-owned `asyncpg.create_pool`
on `app.state`; Depends only reads it. No SQLModel.

- Pros: Matches small-app structure without a second DB stack; typed
  settings; cleaner endpoint signatures; reversible by PR revert
- Cons: Import churn in tests; helper extraction needed to avoid
  circular imports; collector Settings stay dataclass for now

### Option 3: Adopt SQLModel / SQLAlchemy sessions

One engine, `yield` sessions via Depends, models as SQLModel tables.

- Pros: Official FastAPI recommendation; typed rows; future write APIs
  easier
- Cons: Duplicate persistence story vs collector/ML asyncpg SQL;
  migration cost high for existing queries; overkill for <20 read
  endpoints; risk of sync-session mistakes in async routes

### Option 4: Deep package / DDD layering

`domain/`, `application/`, `infrastructure/` under `api/`.

- Pros: Clear theory boundaries
- Cons: Over-engineering for this app size; fights AGENTS.md simplicity

## Decision

**Chosen**: Option 2 — flat FastAPI layout with lifespan-scoped asyncpg
pool injected via `Depends`, pydantic-settings for API env config.

Explicitly defer SQLModel and DDD layering until a write-heavy or
multi-aggregate persistence need appears.

## Consequences

### Positive

- Future agents and humans have a recorded reason not to "just add SQLModel"
- `main.py` becomes an app shell; routers and snapshots become test seams
- Settings validation and defaults live in one module

### Negative / Trade-offs

- Raw SQL + dict rows remain (Pydantic `response_model` only at edges)
- API Settings and collector dataclass Settings are not unified
- Weekly-insights cache may still be read from `app.state` until a later
  Depends alias

## Implementation Notes

- Add `pydantic-settings`; do not replace collector `Settings`
- `get_db_pool(request) -> Pool | None`; never open/close connections in routes
- Router split by content-type/tags: `pages`, `markdown`, `json_api`, `meta`
- Support modules only as needed to break import cycles (`catalog`,
  `snapshots`, `weekly_insights`, `prediction_days`, `pool_page`)
- No intentional URL or response-body changes
- Update `docs/SAD.md` Technology Stack / API notes to cite this ADR
