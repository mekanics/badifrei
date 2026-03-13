# Architecture Review — badi-predictor

**Reviewed:** 2026-02-27  
**Reviewer:** Senior Software Architect (AI Code Review)  
**Scope:** Full system — infrastructure, data pipeline, ML, API, operational concerns

---

## 1. Executive Summary

The project is a well-structured, reasonably clean ML-serving system for pool occupancy prediction. The service separation (collector / API / trainer) is logical, the WebSocket reconnect logic is solid, and the time-based train/test split shows ML discipline. That said, the system has several **production blockers**: hardcoded credentials in `docker-compose.yml`, a trainer service that has no Docker presence (silently absent from the compose stack), a model artifact sharing strategy that only half-works, and a weather feature pipeline that is architecturally disconnected between training and inference. These aren't polish issues — they are gaps that would cause silent failures or incorrect predictions in production.

---

## 2. Critical Issues

### C1 — Hardcoded credentials in `docker-compose.yml`
**File:** `docker-compose.yml` lines 7–9, 27–28  
DB username, password, and database name are `badi:badi` in plaintext, present in the compose file (likely committed to version control). Any access to the repo = full DB access. Needs secrets management (Docker secrets, `.env` file excluded from git, or a vault).

### C2 — Retrainer service is missing from `docker-compose.yml`
**File:** `docker-compose.yml`, `ml/retrain.py`  
`ml/retrain.py` is a fully-implemented APScheduler-based retrainer with a `main()` entry point, but there is **no corresponding service in docker-compose.yml**. The trainer never runs in the composed environment. The model artifact volume (`model_artifacts`) is mounted to the API container but is never written to by any running service. The system will permanently serve "no-model" / zeroed predictions unless someone runs the trainer manually.

### C3 — Model artifact sharing is broken by volume topology
**File:** `docker-compose.yml`, `api/predictor.py` (MODELS_DIR), `ml/train.py` (MODELS_DIR)  
`api/predictor.py` resolves `MODELS_DIR = Path(__file__).parent.parent / "ml" / "models"` — which inside the API container points to `/app/ml/models`. The volume `model_artifacts` is mounted there. But `ml/train.py` writes models to `Path(__file__).parent / "models"` inside whatever container the trainer runs in. If the trainer ever runs as a separate container without the same volume mount, it writes to a different path and the API never sees the new model. This needs explicit coordination — a shared volume mount in both trainer and API containers with the same path, and an explicit volume declaration in a trainer service entry.

### C4 — `lag_1h` feature is always NaN at inference time → silently wrong predictions
**File:** `api/predictor.py` lines 36–43, `ml/features.py` `add_lag_features()`  
At inference, `predictor.predict()` builds a single-row DataFrame. `add_lag_features()` calls `groupby("pool_uid")["occupancy_pct"].shift(1)` — on a 1-row group this always produces `NaN`. This NaN is then `fillna(0)` in `predictor.predict()`. The model was trained knowing that `lag_1h` at a given hour is the previous hour's occupancy (often 30–80%). Replacing it with 0 at inference shifts predictions toward under-estimating occupancy, especially for high-traffic hours. This is **systematic bias** in every prediction.

**Fix required:** At inference time, the API must query the most recent actual reading from the DB and use that as `lag_1h`. Same logic applies to `lag_1w` and `rolling_mean_7d`.

### C5 — Weather features absent from inference
**File:** `api/predictor.py`, `ml/features.py`, `ml/train.py`  
`build_features()` accepts an optional `weather_df` argument. `ml/train.py` does **not** pass weather data during training (calls `build_features(df)` with no weather), so `FEATURE_COLUMNS` does not include weather columns. However, `add_weather_features` and `WEATHER_FEATURE_COLUMNS` exist but are completely unused in the training pipeline. This is a dead code smell — but more importantly, if someone adds weather features to training without also adding them to inference, predictions will silently degrade or crash. The training pipeline and inference path must be kept in strict lockstep.

---

## 3. Significant Concerns

### S1 — DB connection created per-request in `/api/current` endpoint
**File:** `api/main.py` lines 68–85  
Every call to `/api/current` calls `asyncpg.connect(database_url)` — creating a raw connection, not a pool. Under load (e.g., dashboard auto-refresh every 30s × many clients), this creates a new DB connection per request. PostgreSQL's max connections is ~100 by default. Easily exhausted. Fix: create an asyncpg pool at startup (in `lifespan`) and share it.

### S2 — `get_pool()` connection in `data_loader.py` is not pooled
**File:** `ml/data_loader.py` lines 33–35  
`load_data()` calls `asyncpg.connect()` directly. Fine for the one-off training use case, but it doesn't close the connection on `InsufficientDataError` — the `finally` block only runs if `conn` was successfully assigned. Actually, looking more carefully: the `try/finally` block **does** wrap `conn.fetch()` correctly and will close on error. But the pattern is fragile compared to using `async with asyncpg.connect() as conn:` (context manager form). Minor but worth flagging.

### S3 — Weather cache grows forever
**File:** `ml/weather.py` line 20  
`_cache: dict[datetime.date, pd.DataFrame] = {}` is a module-level dict that accumulates indefinitely. For a long-running API/trainer process, this will slowly grow without bound. For the trainer running once a week it's irrelevant, but if the API ever fetches weather for predictions (currently it doesn't, but seems intended), the cache will leak. Cap it with an LRU or a TTL strategy.

### S4 — Weather merge in `add_weather_features` is wrong
**File:** `ml/features.py` lines 88–92  
`add_weather_features()` merges weather by `hour_of_day` only — no date component. This means: for a training set spanning 90 days, **all observations at hour 14 across all 90 days get the same weather value** (the one from `weather_df` for hour 14). This is a fundamental correctness bug if weather data per date is ever passed. The join key must be `(date, hour)`, not just `hour`. The current training pipeline passes no weather data so this bug is dormant — but the code is wrong and will produce leakage when used.

### S5 — Symlink strategy for `model_latest.ubj` is fragile in containers
**File:** `ml/train.py` lines 66–71  
The trainer creates a symlink `model_latest.ubj → model_YYYY-MM-DD.ubj`. Symlinks don't cross Docker volume boundaries cleanly; the target of the symlink is a relative path within the `models/` directory. If the trainer writes to the volume and the API reads it, this works **only** if both containers mount the same volume at the same path. Any path mismatch will cause the API to follow a broken symlink and fail silently (model not loaded). Consider using a regular file copy + atomic rename instead: write to `model_latest.ubj.tmp` then `os.rename()` to `model_latest.ubj`.

### S6 — `DISTINCT ON` query in `/api/current` doesn't use the hypertable index efficiently
**File:** `api/main.py` lines 73–83  
The query uses `DISTINCT ON (pool_uid) ... ORDER BY pool_uid, recorded_at DESC`. The index is on `(pool_uid, time DESC)`, but the column in the query is `recorded_at` — which doesn't exist in the `pool_occupancy` schema (`init.sql` defines column `time`, not `recorded_at`). This query will fail at runtime. Likely a copy-paste error.

### S7 — `pool_uid_encoding` is non-deterministic between training and inference
**File:** `ml/features.py` `get_pool_uid_encoding()` line 19  
The encoding is derived from `sorted(set(uids))` of the **input DataFrame**. During training this is the full pool set. During inference it's a 1-row DataFrame with a single pool UID. The encoded value for any given pool will differ between training and inference unless every pool appears in the inference batch. This is a silent ML bug — the model may produce incorrect predictions because `pool_uid_encoded = 0` at inference refers to a different pool than `pool_uid_encoded = 0` during training. Fix: serialize the uid→int mapping at training time and load it at inference.

### S8 — No trainer service = no model hot-reloading
**File:** `api/predictor.py`, no file watching mechanism exists  
Even if the trainer ran, the API loads the model once at startup (`predictor.load()` in `lifespan`) and never reloads. After retraining, a container restart is required to pick up the new model. This is operationally painful for a weekly retrain cycle. Consider adding a simple file watcher or a `/admin/reload` endpoint (auth-protected).

---

## 4. Minor Issues / Improvements

### M1 — CORS allows all origins
**File:** `api/main.py` lines 43–48  
`allow_origins=["*"]` is fine for dev but should be restricted to known frontend origins in production.

### M2 — No input validation on `pool_uid` parameter
**File:** `api/main.py` `/predict` and `/predict/range` endpoints  
`pool_uid` is read from query params and passed to `predictor.predict()` without sanitisation beyond the pool-existence check. Injection risk is low (no raw SQL here), but the error path if an unknown pool reaches `predictor.predict()` is unclear.

### M3 — `db.py` connection pool never created before first write
**File:** `collector/db.py`  
The asyncpg pool is created lazily on first write. If the DB is temporarily unavailable at startup and becomes available later, the pool creation will be retried — but there's no retry logic in `get_pool()`. A transient DB blip during the first write will raise an exception (caught in `run_collector()`), increment `metrics.errors`, but the pool will remain `None`, so subsequent calls will attempt re-creation. This actually works in practice, but it's accident-driven rather than intentional. Make the pool creation explicit with retry logic.

### M4 — Training and testing on the same feature object causes subtle leakage
**File:** `ml/train.py` `train()` function  
`build_features(df)` is called on the full dataset before the train/test split. `add_rolling_features()` computes a 7-day rolling mean on the entire dataset — meaning the rolling mean for training rows is computed using values from the test period. This is **lookahead leakage** in the rolling features. The rolling mean and lag features should be computed separately for train and test, or using only past data. In practice the bias may be small, but it inflates training metrics.

### M5 — `_pools_cache` is a module-level global, not thread-safe
**File:** `api/main.py` line 30  
`_pools_cache` is set without locking. Under asyncio this is safe (single-threaded event loop), but it's a latent bug if the codebase ever moves to multi-threaded workers.

### M6 — Health check for collector reports `running: True` even after error spiral
**File:** `collector/main.py`  
`metrics.running` is only set `False` when `run_collector()` exits cleanly. If the WebSocket loop is stuck retrying, `running` will remain `True` even though no data is being collected.

### M7 — APScheduler job failure is not alarmed
**File:** `ml/retrain.py`  
If `retrain_job()` raises an unhandled exception, APScheduler will log it and continue scheduling future runs. There's no alerting. A persistent failure (e.g., DB gone) will silently produce no new models while the scheduler happily claims it's running.

### M8 — `db_test` container exposed on port 5433 with default credentials
**File:** `docker-compose.yml` lines 18–29  
Test DB is exposed on the host with `badi:badi` credentials. Fine for local dev, dangerous if deployed on a non-firewalled machine.

### M9 — No DB migration strategy
There is only `init.sql`. Schema changes require manual intervention or dropping/recreating the container. There's no migration tool (Alembic, Flyway) referenced anywhere.

---

## 5. What's Done Well

- **WebSocket reconnect with exponential backoff** (`ws_client.py`): The backoff logic (1s → 2s → ... → 60s cap) is correct and prevents log spam on persistent failures. `ConnectionClosed` and generic exceptions are handled separately, which is the right pattern.

- **Time-based train/test split** (`train.py` `time_based_split()`): The comment is explicit ("NEVER use random split for time series (data leakage!)") and the implementation is correct. This shows genuine ML awareness.

- **Deduplication in the collector** (`main.py` `should_write()`): Writing only on state change plus a 15-minute force interval is a smart strategy that balances DB load against data freshness. Clean, testable function.

- **Structured JSON logging** (`collector/main.py`): JSON-formatted log output is production-ready and compatible with log aggregation tools (Loki, CloudWatch, etc.).

- **TimescaleDB hypertable + index** (`init.sql`): Using a hypertable on `time` with a `(pool_uid, time DESC)` index is exactly right for this access pattern. The generated `occupancy_pct` column avoids stale derived data.

- **`InsufficientDataError` guard in `data_loader.py`**: Explicit minimum record check with a clear, human-readable error message is good defensive programming for the cold-start case.

- **Model file pruning** (`retrain.py` `_prune_old_models()`): Disk space management is often forgotten. The 30-day retention policy is reasonable.

- **Health endpoints with meaningful metrics**: The collector's `/health` endpoint returns `records_written`, `errors`, and `last_write` — genuinely useful for monitoring, not just a 200 OK.

- **Test coverage breadth**: A `tests/unit/` directory with tests for most key modules (features, deduplication, weather, schema, etc.) shows the project takes testing seriously.

---

## 6. Recommended Next Actions (Prioritized)

### P1 — Fix `pool_uid_encoding` serialization (Silent ML bug, wrong predictions)
In `ml/train.py::save_model()`, serialize the uid→int mapping alongside the model file. In `api/predictor.py::load()`, deserialize and use it. Until this is fixed, `pool_uid_encoded` is meaningless at inference time.

### P2 — Fix `lag_1h` at inference time (Systematic prediction bias)
In `api/predictor.py::predict()`, query the DB for the most recent actual occupancy reading for the given `pool_uid` and use it as `lag_1h`. Similarly reconstruct `rolling_mean_7d` from recent data or cache it. This will be the single biggest accuracy improvement possible without retraining.

### P3 — Add trainer service to `docker-compose.yml` + fix volume topology
Create a `trainer` service entry that mounts `model_artifacts:/app/ml/models` and runs `ml/retrain.py`. Ensure the API service mounts the same named volume at the same path. Verify the symlink-vs-atomic-rename question (see S5).

### P4 — Fix the `/api/current` endpoint: connection pool + column name bug
Replace `asyncpg.connect()` with a shared pool (created in `lifespan`). Fix the `recorded_at` → `time` column name typo. Both are immediate runtime failures.

### P5 — Move credentials out of `docker-compose.yml`
Use a `.env` file (gitignored) or Docker secrets. Add a `.env.example` with placeholder values to the repo. At minimum, add `.env` to `.gitignore` and document the setup process.

---

*End of review. Total: 5 critical, 8 significant, 9 minor issues identified.*
