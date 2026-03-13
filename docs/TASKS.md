# TASKS.md — Zürich Pool Occupancy Prediction System

A TDD-first task breakdown for the badi-predictor project. The system collects real-time pool occupancy data from a WebSocket API, stores it in TimescaleDB, trains an XGBoost model to predict future occupancy, and exposes predictions via FastAPI.

**Convention:** Every coding task starts with writing tests. Red → Green → Refactor.

---

## Phase 1 — Infrastructure & Data Collection

### TASK-001: Project scaffold & tooling

**Phase:** 1  
**Status:** DONE  
**Dependencies:** none

**Description:**
Create the project structure, virtual environment, dependency management (uv or pip + pyproject.toml), pytest config, and pre-commit hooks (ruff, black).

**TDD — Write These Tests First:**
- `test_imports`: verify all key dependencies import without error
- `test_config_from_env`: verify config reads WS_URL and DATABASE_URL from env vars with sensible defaults

**Acceptance Criteria:**
- [ ] `pyproject.toml` with all dependencies pinned
- [ ] `pytest` runs with zero errors on empty test suite
- [ ] `ruff` and `black` configured and passing
- [ ] `.env.example` with all required env vars documented
- [ ] `Makefile` with `make test`, `make lint`, `make run-collector`

**Implementation Notes:**
Use `uv` for package management — much faster than pip. Structure: `collector/`, `ml/`, `api/`, `tests/`, `docker/`.

---

### TASK-002: Docker Compose + TimescaleDB setup

**Phase:** 1  
**Status:** DONE  
**Dependencies:** TASK-001

**Description:**
Create `docker-compose.yml` with TimescaleDB service. Write DB init SQL (schema + hypertable). Verify connection from host.

**TDD — Write These Tests First:**
- `test_db_connection`: connects to TimescaleDB using test DATABASE_URL
- `test_hypertable_exists`: queries `timescaledb_information.hypertables` and asserts `pool_occupancy` is present
- `test_schema_columns`: verifies all expected columns exist with correct types

**Acceptance Criteria:**
- [ ] `docker compose up db` starts TimescaleDB successfully
- [ ] Init SQL runs automatically on first start
- [ ] `pool_occupancy` hypertable created with correct schema
- [ ] Index on `(pool_uid, time DESC)` present
- [ ] Tests pass against a live test DB (use pytest fixture with test DB)

**Implementation Notes:**
Use `timescale/timescaledb:latest-pg16` image. Mount init SQL via `/docker-entrypoint-initdb.d/`. Use a separate `TEST_DATABASE_URL` for tests.

---

### TASK-003: Database writer module

**Phase:** 1  
**Status:** DONE  
**Dependencies:** TASK-002

**Description:**
Implement `collector/db.py` — async module that accepts a list of pool records and bulk-inserts into `pool_occupancy` with a timestamp.

**TDD — Write These Tests First:**
- `test_insert_single_record`: inserts one valid record, reads it back, asserts fields match
- `test_insert_batch`: inserts 22 records (full API response), verifies count
- `test_skip_zero_maxspace`: records with `max_space=0` are skipped (no division by zero)
- `test_timestamp_utc`: inserted `time` column is timezone-aware UTC
- `test_duplicate_handling`: inserting same data twice doesn't crash (ON CONFLICT DO NOTHING)

**Acceptance Criteria:**
- [ ] `write_batch(records: list[dict], timestamp: datetime)` function implemented
- [ ] All 5 tests pass against test DB
- [ ] Pools with `max_space=0` are silently skipped
- [ ] Uses `asyncpg` connection pool (not per-insert connections)

**Implementation Notes:**
Use `asyncpg.create_pool()` at startup. Bulk insert with `executemany` or `copy_records_to_table` for performance.

---

### TASK-004: WebSocket client module

**Phase:** 1  
**Status:** DONE  
**Dependencies:** TASK-001

**Description:**
Implement `collector/ws_client.py` — async WebSocket client that connects to `wss://badi-public.crowdmonitor.ch:9591/api`, sends `"all"`, and yields parsed messages. Includes reconnect logic with exponential backoff.

**TDD — Write These Tests First:**
- `test_parse_valid_message`: given a valid JSON string, returns list of pool dicts with correct fields
- `test_parse_missing_fields`: message missing `uid` or `currentfill` raises `ValueError`
- `test_parse_negative_currentfill`: `currentfill < 0` is clamped to 0
- `test_reconnect_called_on_close`: mock WS that closes immediately; verify reconnect is attempted
- `test_backoff_timing`: verify exponential backoff delays (mock `asyncio.sleep`, assert called with increasing values)

**Acceptance Criteria:**
- [ ] `connect_and_stream()` async generator yields validated pool record lists
- [ ] All 5 tests pass (WS mocked with `pytest-asyncio` + mock)
- [ ] Reconnect with backoff: 1s → 2s → 4s → … → max 60s
- [ ] Logs connect/disconnect/reconnect events

**Implementation Notes:**
Use `websockets` library. Mock with `unittest.mock.AsyncMock`. Use `tenacity` for retry logic — cleaner than manual backoff.

---

### TASK-005: Collector main entry point

**Phase:** 1  
**Status:** DONE  
**Dependencies:** TASK-003, TASK-004

**Description:**
Implement `collector/main.py` — wires WS client and DB writer together. Reads each message from the stream and writes to DB. Handles graceful shutdown on SIGTERM.

**TDD — Write These Tests First:**
- `test_message_flows_to_db`: mock WS client yielding 1 batch; assert `write_batch` called once with correct args
- `test_shutdown_on_sigterm`: send SIGTERM to running coroutine; assert it exits cleanly within 2s
- `test_healthcheck_endpoint`: `/health` returns 200 when collector is running (simple HTTP server on port 8080)

**Acceptance Criteria:**
- [ ] `python -m collector.main` starts and begins collecting
- [ ] All 3 tests pass
- [ ] `/health` HTTP endpoint returns `{"status": "ok", "last_write": "<ISO timestamp>"}`
- [ ] Graceful shutdown flushes pending writes before exit

---

### TASK-006: Docker Compose — collector service

**Phase:** 1  
**Status:** TODO  
**Dependencies:** TASK-005, TASK-002

**Description:**
Add `collector` service to `docker-compose.yml`. Build Dockerfile for collector. Verify end-to-end: compose up → data flows into DB.

**TDD — Write These Tests First:**
- `test_collector_health`: after `docker compose up`, GET `http://localhost:8080/health` returns 200
- `test_data_in_db`: after 60s uptime, `SELECT COUNT(*) FROM pool_occupancy` returns > 0

**Acceptance Criteria:**
- [ ] `docker compose up` starts both `db` and `collector` cleanly
- [ ] Collector reconnects if DB isn't ready yet (retry on startup)
- [ ] Data visible in DB within 2 minutes of startup
- [ ] `restart: always` set on collector service

**Implementation Notes:**
Use `depends_on` with `healthcheck` on the DB service to avoid race conditions on startup.

---

### TASK-007: Data validation & observability

**Phase:** 1  
**Status:** DONE  
**Dependencies:** TASK-005

**Description:**
Add structured logging (JSON format), track metrics (records written, errors, last successful write), and validate incoming data with Pydantic.

**TDD — Write These Tests First:**
- `test_pydantic_model_valid`: valid dict parses into `PoolReading` model
- `test_pydantic_model_invalid_uid`: missing `uid` raises `ValidationError`
- `test_log_output_is_json`: captured log output is valid JSON with `timestamp`, `level`, `message` fields
- `test_write_counter_increments`: after 3 batches written, `metrics.records_written == 66` (22 pools × 3)

**Acceptance Criteria:**
- [ ] `PoolReading` Pydantic model validates all incoming records
- [ ] Invalid records logged as warnings and skipped (not crashed)
- [ ] Structured JSON logging to stdout
- [ ] `/health` endpoint includes `records_written` and `errors` counts

---

## Phase 2 — ML Model

### TASK-008: Feature engineering module

**Phase:** 2  
**Status:** DONE  
**Dependencies:** TASK-003

**Description:**
Implement `ml/features.py` — extracts and transforms raw DB records into ML-ready feature matrix.

**TDD — Write These Tests First:**
- `test_time_features`: given a timestamp, returns correct `hour_of_day`, `day_of_week`, `is_weekend`, `month`
- `test_is_holiday_zurich`: 2026-01-01 (New Year) returns `is_holiday=True`; 2026-02-27 (random Friday) returns `False`
- `test_pool_uid_encoding`: same `pool_uid` always maps to same integer; unseen uid gets a new integer
- `test_lag_features`: given ordered time series, `lag_1h` correctly reflects value from 1h prior (NaN for first entry)
- `test_rolling_mean`: 7-day rolling mean calculated correctly over known data
- `test_feature_matrix_shape`: given 100 raw records for 5 pools, output DataFrame has correct shape and no unexpected NaNs

**Acceptance Criteria:**
- [ ] All 6 tests pass
- [ ] `build_features(df: pd.DataFrame) -> pd.DataFrame` function
- [ ] Feature list documented in module docstring
- [ ] Pool type (Hallenbad/Freibad/Strandbad) derived from a static mapping file `ml/pool_metadata.json`

**Implementation Notes:**
Swiss public holidays: use `holidays` Python package (`holidays.Switzerland(prov='ZH')`). Lag features require the data to be sorted by `(pool_uid, time)` first.

---

### TASK-009: Data loader (DB → DataFrame)

**Phase:** 2  
**Status:** DONE  
**Dependencies:** TASK-003, TASK-008

**Description:**
Implement `ml/data_loader.py` — queries TimescaleDB and returns a clean pandas DataFrame ready for feature engineering.

**TDD — Write These Tests First:**
- `test_load_returns_dataframe`: returns `pd.DataFrame` with expected columns
- `test_load_date_range`: `load_data(start, end)` only returns records within range
- `test_load_excludes_zero_maxspace`: pools with `max_space=0` filtered out
- `test_load_min_records_check`: raises `InsufficientDataError` if fewer than 1000 records returned

**Acceptance Criteria:**
- [ ] `load_data(start: datetime, end: datetime) -> pd.DataFrame`
- [ ] Runs against test DB with seeded fixture data
- [ ] All 4 tests pass

---

### TASK-010: Model training script

**Phase:** 2  
**Status:** DONE  
**Dependencies:** TASK-008, TASK-009

**Description:**
Implement `ml/train.py` — loads data, builds features, trains XGBoost regressor, evaluates on holdout set, saves model artifact.

**TDD — Write These Tests First:**
- `test_train_returns_model`: `train(df)` returns a fitted XGBoost model object
- `test_model_predicts_in_range`: predictions on test set are between 0 and 100
- `test_evaluation_metrics_logged`: training logs MAE and RMSE per pool to stdout
- `test_model_saved`: after training, `models/model_latest.ubj` exists and is loadable
- `test_train_test_split`: split is time-based (last 20% by time, not random shuffle)

**Acceptance Criteria:**
- [ ] `python -m ml.train` runs end-to-end (requires data in DB)
- [ ] All 5 tests pass (use synthetic fixture data for unit tests)
- [ ] Model artifact saved to `ml/models/model_YYYY-MM-DD.ubj` + symlink `model_latest.ubj`
- [ ] Training summary (MAE, RMSE, feature importance top-10) written to `ml/models/training_report.json`

**Implementation Notes:**
Time-based train/test split is critical — random split would leak future data into training. Use last 20% of time range as test set.

---

### TASK-011: Model evaluation & baseline comparison

**Phase:** 2  
**Status:** TODO  
**Dependencies:** TASK-010

**Description:**
Implement `ml/evaluate.py` — evaluates model against a naive baseline (predict last week's value at same time) and per-pool breakdown.

**TDD — Write These Tests First:**
- `test_mae_better_than_naive`: trained model MAE < naive baseline MAE on test set
- `test_per_pool_metrics`: returns dict keyed by `pool_uid` with `mae` and `rmse`
- `test_worst_pool_identified`: identifies pool with highest MAE (expected: Freibäder in off-season)

**Acceptance Criteria:**
- [ ] `evaluate(model, X_test, y_test) -> EvaluationReport`
- [ ] Model beats naive baseline on MAE
- [ ] Per-pool breakdown included in training report
- [ ] All 3 tests pass

---

### TASK-012: Pool metadata file

**Phase:** 2  
**Status:** DONE  
**Dependencies:** none

**Description:**
Create `ml/pool_metadata.json` — static mapping of all 22 pool UIDs to name, type (Hallenbad/Freibad/Strandbad), and seasonal availability.

**TDD — Write These Tests First:**
- `test_all_known_uids_present`: all 22 UIDs from WS API are in metadata
- `test_pool_type_valid`: every entry has `type` in `["hallenbad", "freibad", "strandbad"]`
- `test_seasonal_flags`: Freibäder marked `seasonal: true`, Hallenbäder `seasonal: false`

**Acceptance Criteria:**
- [ ] `ml/pool_metadata.json` with all 22 pools
- [ ] All 3 tests pass
- [ ] UIDs: SSD-1 through SSD-11, fb008, fb012, LETZI-1, SSD-11, fb018, seb6946, seb6947, seb6948, WEN-1, HUENENBERG-1, LIDO-1, RISCH-1, SSD-10

---

## Phase 3 — Prediction API

### TASK-013: FastAPI app scaffold

**Phase:** 3  
**Status:** DONE  
**Dependencies:** TASK-001

**Description:**
Create `api/main.py` — FastAPI app with `/health` endpoint, Pydantic schemas, and CORS config.

**TDD — Write These Tests First:**
- `test_health_returns_200`: GET `/health` returns `{"status": "ok"}`
- `test_openapi_schema_available`: GET `/docs` returns 200
- `test_cors_header`: response includes CORS header for `*`

**Acceptance Criteria:**
- [ ] FastAPI app starts with `uvicorn api.main:app`
- [ ] All 3 tests pass using `httpx` + `pytest-asyncio`
- [ ] Auto-generated OpenAPI docs at `/docs`

---

### TASK-014: `/pools` endpoint

**Phase:** 3  
**Status:** DONE  
**Dependencies:** TASK-013, TASK-012

**Description:**
Implement `GET /pools` — returns list of all pools with uid, name, type, seasonal flag.

**TDD — Write These Tests First:**
- `test_pools_returns_list`: response is a JSON array
- `test_pools_count`: returns exactly 22 pools
- `test_pools_schema`: each item has `uid`, `name`, `type`, `seasonal` fields
- `test_pools_hallenbad_count`: exactly the right number of Hallenbäder

**Acceptance Criteria:**
- [ ] `GET /pools` returns all 22 pools
- [ ] All 4 tests pass
- [ ] Response cached (no DB call needed — static data)

---

### TASK-015: `/predict` endpoint (single prediction)

**Phase:** 3  
**Status:** DONE  
**Dependencies:** TASK-013, TASK-010

**Description:**
Implement `GET /predict?pool_uid={uid}&datetime={ISO8601}` — loads model, builds features for the requested datetime, returns prediction.

**TDD — Write These Tests First:**
- `test_predict_returns_float`: response `predicted_occupancy_pct` is a float between 0 and 100
- `test_predict_unknown_pool_uid`: returns 404 with clear error message
- `test_predict_past_datetime_allowed`: prediction for past datetime still works (useful for debugging)
- `test_predict_invalid_datetime`: malformed datetime string returns 422
- `test_predict_response_schema`: response includes `pool_uid`, `pool_name`, `predicted_at`, `predicted_occupancy_pct`, `model_version`

**Acceptance Criteria:**
- [ ] `GET /predict?pool_uid=SSD-5&datetime=2026-03-07T15:00:00` returns valid prediction
- [ ] All 5 tests pass
- [ ] Model loaded once at startup (not on every request)
- [ ] `model_version` field reflects the training date

---

### TASK-016: `/predict/range` endpoint (full day)

**Phase:** 3  
**Status:** DONE  
**Dependencies:** TASK-015

**Description:**
Implement `GET /predict/range?pool_uid={uid}&date={YYYY-MM-DD}` — returns hourly predictions for an entire day.

**TDD — Write These Tests First:**
- `test_range_returns_24_entries`: response array has exactly 24 items (one per hour)
- `test_range_hours_sequential`: hours 0–23 present in order
- `test_range_all_in_bounds`: all predictions between 0 and 100
- `test_range_invalid_date`: `date=not-a-date` returns 422

**Acceptance Criteria:**
- [ ] `GET /predict/range?pool_uid=SSD-5&date=2026-03-07` returns 24 hourly predictions
- [ ] All 4 tests pass
- [ ] Response time < 500ms (batch inference, not 24 serial calls)

---

### TASK-017: Docker Compose — API service

**Phase:** 3  
**Status:** TODO  
**Dependencies:** TASK-016, TASK-006

**Description:**
Add `api` service to `docker-compose.yml`. Model artifacts mounted as volume. Verify end-to-end prediction via compose.

**TDD — Write These Tests First:**
- `test_api_health_via_compose`: `http://localhost:8000/health` returns 200 after compose up
- `test_predict_via_compose`: `/predict` returns valid response (requires trained model artifact present)

**Acceptance Criteria:**
- [ ] `docker compose up` starts `db`, `collector`, and `api`
- [ ] API available at `http://localhost:8000`
- [ ] Model volume correctly mounted
- [ ] All tests pass

---

## Phase 4 — Polish & Ops

### TASK-018: Automated retraining cron

**Phase:** 4  
**Status:** TODO  
**Dependencies:** TASK-010, TASK-017

**Description:**
Add a `trainer` service (or cron job) that retrains the model weekly and updates the `model_latest.ubj` symlink.

**TDD — Write These Tests First:**
- `test_retrain_script_exits_zero`: `python -m ml.train` exits with code 0 on sufficient data
- `test_new_model_artifact_created`: after retrain, a new dated artifact exists alongside `model_latest`
- `test_api_picks_up_new_model`: after model file is replaced, next API call uses new model (no restart required)

**Acceptance Criteria:**
- [ ] Weekly cron (APScheduler or Docker + cron) triggers retrain
- [ ] Old model artifacts retained for 30 days (then pruned)
- [ ] API hot-reloads model without restart
- [ ] All 3 tests pass

---

### TASK-019: Weather feature integration (Open-Meteo)

**Phase:** 4  
**Status:** TODO  
**Dependencies:** TASK-008, TASK-010

**Description:**
Add daily weather features (max temp, sunshine hours) from Open-Meteo free API. Enrich feature matrix. Retrain and evaluate improvement.

**TDD — Write These Tests First:**
- `test_fetch_weather_returns_dataframe`: `fetch_weather(start, end)` returns DataFrame with `date`, `temp_max`, `sunshine_hours`
- `test_weather_cached_locally`: second call within same day uses cached data (no HTTP request)
- `test_model_with_weather_not_worse`: MAE with weather features ≤ MAE without (on test set)

**Acceptance Criteria:**
- [ ] Weather data fetched from `https://api.open-meteo.com/v1/forecast` (free, no key)
- [ ] Weather features added to `build_features()`
- [ ] All 3 tests pass
- [ ] Retrained model evaluated — weather feature importance logged

**Implementation Notes:**
Open-Meteo historical: `https://archive-api.open-meteo.com/v1/archive`. Forecast: standard endpoint. Cache in `ml/data/weather_cache.parquet`.

---

### TASK-020: Collector deduplication

**Phase:** 4  
**Status:** TODO  
**Dependencies:** TASK-003

**Description:**
Skip DB writes when pool occupancy hasn't changed since last write. Reduces storage bloat.

**TDD — Write These Tests First:**
- `test_no_write_if_unchanged`: if `currentfill` same as last record, `write_batch` not called
- `test_write_if_changed`: if any pool in batch changed, full batch written
- `test_always_write_on_interval`: even if unchanged, write every 15 minutes (keep time resolution)

**Acceptance Criteria:**
- [ ] Deduplication logic implemented in `collector/main.py`
- [ ] All 3 tests pass
- [ ] DB growth rate measurably reduced (log writes/hour)

---

### TASK-021: Integration test suite

**Phase:** 4  
**Status:** TODO  
**Dependencies:** TASK-017

**Description:**
Write end-to-end integration tests that run against a live `docker compose` stack.

**TDD — Write These Tests First:**
- `test_e2e_data_collection`: start compose, wait 2 min, verify records in DB
- `test_e2e_prediction`: call `/predict` with real pool UID, get valid response
- `test_e2e_range`: call `/predict/range`, get 24 valid hourly predictions
- `test_e2e_collector_reconnects`: stop DB for 30s, restart it, verify collector resumes writing

**Acceptance Criteria:**
- [ ] All 4 integration tests pass
- [ ] Tests marked with `@pytest.mark.integration` and skipped by default (run with `make test-integration`)
- [ ] CI-friendly: tests clean up after themselves

---

### TASK-022: README & developer docs

**Phase:** 4  
**Status:** DONE  
**Dependencies:** TASK-017

**Description:**
Write `README.md` with quickstart, architecture diagram (ASCII), env vars reference, and how to run tests.

**Acceptance Criteria:**
- [ ] `README.md` covers: what it is, quickstart (`docker compose up`), env vars, running tests, API reference
- [ ] ASCII architecture diagram showing WS → Collector → TimescaleDB → ML Trainer → FastAPI
- [ ] `CONTRIBUTING.md` with TDD workflow guide

---

### TASK-023: Persist weather data to TimescaleDB

**Phase:** 4  
**Status:** DONE  
**Dependencies:** TASK-002, TASK-019

**Description:**
Replace the in-memory-only weather cache in `ml/weather.py` with a persistent `hourly_weather` TimescaleDB table.
Currently every training run re-fetches all historical dates from Open-Meteo, which becomes expensive as the dataset grows across years.
`fetch_weather_batch()` should check the DB first, fetch only missing dates from Open-Meteo, then persist the new rows.

**TDD — Write These Tests First:**
- `test_hourly_weather_table_exists`: queries `timescaledb_information.hypertables` and asserts `hourly_weather` is present
- `test_hourly_weather_schema`: verifies columns `date DATE`, `hour SMALLINT`, `temperature_c FLOAT`, `precipitation_mm FLOAT`, `weathercode SMALLINT` exist with correct types; primary key `(date, hour)` prevents duplicates
- `test_fetch_weather_batch_writes_to_db`: after calling `fetch_weather_batch([some_date])` with an empty DB, the rows are present in `hourly_weather`
- `test_fetch_weather_batch_reads_from_db`: seed `hourly_weather` with data for a date; assert `fetch_weather_batch([that_date])` returns those rows and makes **zero** HTTP requests to Open-Meteo (mock `aiohttp.ClientSession`)
- `test_fetch_weather_batch_partial_cache_hit`: seed DB with dates A and C; request dates A, B, C; assert Open-Meteo is called **only** for date B, and all three dates are present in the returned DataFrame
- `test_nan_rows_not_persisted`: when Open-Meteo returns an error (HTTP 500) for a date, the NaN fallback rows are returned to the caller but **not** written to `hourly_weather` (don't poison the cache)

**Acceptance Criteria:**
- [ ] New SQL migration/init script: `docker/init-weather.sql` (or appended to existing init SQL) creates `hourly_weather` table as a TimescaleDB hypertable, partitioned on `date`, with a unique index on `(date, hour)`
- [ ] `fetch_weather_batch()` in `ml/weather.py` updated: check DB → fetch missing from Open-Meteo → persist new rows → return combined DataFrame
- [ ] DB pool/connection reuses the existing `asyncpg` infrastructure (no second connection string)
- [ ] In-memory `_cache` dict retained as a hot layer in front of the DB (avoids DB round-trips for dates already loaded in the current process)
- [ ] All 6 tests pass against a live test DB fixture
- [ ] `clear_cache()` utility extended to also truncate `hourly_weather` in test environments (guarded by an env flag, e.g. `WEATHER_CACHE_DB_TRUNCATE_ON_CLEAR=true`)

**Implementation Notes:**
Use `INSERT … ON CONFLICT (date, hour) DO NOTHING` for upserts so concurrent retrains don't race. Keep the write path async — bulk insert with `asyncpg.executemany`. The DB check should query `SELECT date FROM hourly_weather WHERE date = ANY($1)` to find which requested dates are already fully cached before deciding what to fetch.

---

### TASK-024: Fix automated retrainer to pass weather features

**Phase:** 4  
**Status:** DONE  
**Dependencies:** TASK-018, TASK-019, TASK-023

**Description:**
`ml/retrain.py` calls `train(df)` without fetching or passing `weather_df`, so weather features are silently absent from every automated retrain.
This produces an inferior model compared to the manual `scripts/train.py` run which correctly fetches weather.
Fix `retrain_job()` to mirror the weather-fetch pattern from `scripts/train.py`, with graceful degradation if the fetch fails.

**TDD — Write These Tests First:**
- `test_retrain_job_calls_fetch_weather_batch`: mock `fetch_weather_batch` and `train`; run `retrain_job()`; assert `fetch_weather_batch` is called with the unique dates extracted from the loaded DataFrame
- `test_retrain_job_passes_weather_df_to_train`: assert `train()` is called with a `weather_df` kwarg equal to the DataFrame returned by the mock `fetch_weather_batch`
- `test_retrain_job_continues_without_weather_on_fetch_failure`: make `fetch_weather_batch` raise an `Exception`; assert `retrain_job()` does **not** raise, logs a warning containing "weather", and calls `train(df, weather_df=None)` (or no `weather_df` kwarg)
- `test_retrain_job_continues_without_weather_on_partial_nan`: make `fetch_weather_batch` return a DataFrame where all weather columns are NaN; assert `train()` is still called (NaN weather is handled downstream by feature engineering, not by crashing the retrainer)
- `test_retrain_job_weather_dates_match_training_set`: the dates passed to `fetch_weather_batch` are exactly `pd.to_datetime(df["time"]).dt.date.unique()` — no more, no less

**Acceptance Criteria:**
- [ ] `retrain_job()` in `ml/retrain.py` fetches weather for the loaded training set using `fetch_weather_batch(unique_dates)` before calling `train()`
- [ ] Weather fetch wrapped in `try/except`; on any failure: log `WARNING` with the exception message, set `weather_df = None`, continue to `train(df, weather_df=None)`
- [ ] `train()` call updated to `train(df, weather_df=weather_df)` (matching the signature already used in `scripts/train.py`)
- [ ] Training log output includes a line confirming weather fetch success (e.g. `"Weather fetched for N dates"`) or degraded mode (e.g. `"Training without weather features (fetch failed)"`)
- [ ] All 5 tests pass using mocked DB/weather/train dependencies (no live DB required for unit tests)
- [ ] `scripts/train.py` and `ml/retrain.py` are now in parity on the weather-fetch pattern — consider extracting a shared `_fetch_weather_for_df(df)` helper to `ml/weather.py` or a new `ml/training_utils.py` to avoid future drift

**Implementation Notes:**
The fix is small (~10 lines) but the test coverage is important — the bug was silent for as long as it existed because `train()` accepts `weather_df=None` without error. Extract the pattern into a shared helper so the two callers can't diverge again.

---

### TASK-025: Lightweight DB migration runner

**Phase:** 4  
**Status:** ✅ DONE  
**Dependencies:** TASK-002, TASK-023

**Description:**
The project runs on Coolify with an existing live database. `init.sql` only fires on a fresh container (via `/docker-entrypoint-initdb.d/`), so schema changes introduced after initial deploy — such as the `hourly_weather` hypertable from TASK-023 — are silently skipped on existing deployments. This task introduces a proper, dependency-free migration runner so schema evolution is safe, reproducible, and applied automatically on every deploy.

**TDD — Write These Tests First:**
- `test_schema_migrations_table_created`: running the migrator on an empty DB creates the `schema_migrations` table with columns `filename TEXT PRIMARY KEY`, `applied_at TIMESTAMPTZ NOT NULL`
- `test_migrations_applied_in_order`: place three migration files named `001_`, `002_`, `003_` in the migrations dir; assert they are executed in lexicographic filename order and all appear in `schema_migrations`
- `test_idempotent_on_rerun`: run migrator twice against the same DB; assert each migration is applied exactly once (`SELECT COUNT(*)` per filename returns 1, no duplicate-key errors)
- `test_skips_already_applied`: seed `schema_migrations` with `001_init.sql`; run migrator; assert only `002_hourly_weather.sql` is executed (mock/capture SQL calls to verify `001` is never re-run)
- `test_failed_migration_halts_runner`: make `002_` contain invalid SQL; assert migrator exits non-zero, `003_` is never executed, and `002_` is **not** recorded in `schema_migrations` (transaction rollback)
- `test_empty_migrations_dir_is_safe`: point migrator at an empty directory; assert it exits zero with no errors

**Acceptance Criteria:**
- [ ] `scripts/migrate.py` implemented — scans `docker/migrations/` in filename order, creates `schema_migrations` table if absent, applies only unaprecorded migrations, records each in `schema_migrations` on success
- [ ] Existing SQL files moved/renamed to versioned migration files: `docker/migrations/001_init.sql` (from `docker/init.sql`) and `docker/migrations/002_hourly_weather.sql` (from `docker/init-weather.sql`)
- [ ] `docker-compose.yml` gains a `migrator` one-shot service that builds from the project image, runs `python scripts/migrate.py`, and exits; all other services (`collector`, `api`, `retrainer`) declare `depends_on: migrator: condition: service_completed_successfully`
- [ ] Each migration file is executed inside a single transaction; on SQL error the transaction rolls back, the filename is not written to `schema_migrations`, and the runner exits with a non-zero code
- [ ] `COOLIFY.md` (or a section in `README.md`) documents the Coolify pre-deploy step: set the deploy command to `python scripts/migrate.py` so migrations run before the new image goes live
- [ ] No new Python dependencies — uses only `asyncpg` (already in `pyproject.toml`) and the stdlib
- [ ] All 6 tests pass with a live test-DB pytest fixture (same pattern as TASK-002/TASK-003); tests are isolated — each creates a fresh schema and tears down after

**Implementation Notes:**
`schema_migrations` creation should itself be idempotent (`CREATE TABLE IF NOT EXISTS`). Scan with `sorted(Path("docker/migrations").glob("*.sql"))` to guarantee lexicographic order regardless of filesystem. The migrator should accept an optional `--migrations-dir` CLI argument so tests can point it at a temp directory with fixture SQL files. For the Coolify pre-deploy command: Coolify supports a "Pre-deploy Command" field in the service settings — document the exact field name and value (`python scripts/migrate.py`). Consider logging each applied migration name to stdout so deploy logs are self-documenting.

---

### TASK-026: Per-city weather fetching

**Phase:** 4  
**Status:** ✅ DONE  
**Dependencies:** TASK-023, TASK-024, TASK-025

**Description:**
`ml/weather.py` currently hardcodes a single Zürich coordinate (`lat=47.3769, lon=8.5417`) for all weather fetches. However, `ml/pool_metadata.json` contains pools from 8 distinct cities (zurich, bern, adliswil, luzern, entfelden, hunenberg, rotkreuz, wengen). Pools in Bern, Luzern, and other cities are silently receiving incorrect Zürich weather data, degrading prediction accuracy for those pools. This task introduces city-level weather granularity: one weather record per (city, date, hour) instead of one global record per (date, hour).

The `hourly_weather` table has **not** been deployed to production yet, so `docker/migrations/002_hourly_weather.sql` may be updated in place — no new migration file is needed.

**TDD — Write These Tests First:**
- `test_city_coords_all_cities_present`: assert `CITY_COORDS` in `weather.py` contains keys for all 8 city slugs (`zurich`, `bern`, `adliswil`, `luzern`, `entfelden`, `hunenberg`, `rotkreuz`, `wengen`) and each value is a `(float, float)` tuple
- `test_fetch_weather_batch_uses_city_coords`: mock the Open-Meteo HTTP call; call `fetch_weather_batch(dates, city="bern")`; assert the request URL contains Bern's lat/lon, not Zürich's
- `test_fetch_weather_batch_default_city_is_zurich`: call `fetch_weather_batch(dates)` without a `city` argument; assert it uses `CITY_COORDS["zurich"]` (backward-compat default)
- `test_persist_weather_includes_city`: mock `asyncpg`; call `persist_weather(df, city="luzern")`; assert the INSERT statement includes `city` and the rows contain `"luzern"`
- `test_load_cached_dates_filters_by_city`: seed `hourly_weather` with rows for `city="zurich"` on date `2025-06-01`; call `load_cached_dates(["2025-06-01"], city="bern")`; assert the date is **not** returned as cached (different city)
- `test_load_cached_dates_hits_cache_for_matching_city`: seed rows for `city="bern"` on `2025-06-01`; assert `load_cached_dates(["2025-06-01"], city="bern")` returns that date as cached
- `test_fetch_weather_for_df_multi_city`: build a mock DataFrame with pools from `zurich` and `bern`; assert `_fetch_weather_for_df(df)` calls `fetch_weather_batch` once per city (2 calls), with the correct city slug each time
- `test_training_join_uses_city_and_date_hour`: verify that after `_fetch_weather_for_df`, weather is joined to the training DataFrame on `(city, date, hour)` — pools from `bern` receive `bern` weather rows, not `zurich` rows

**Acceptance Criteria:**
- [ ] `CITY_COORDS` dict added to `ml/weather.py` mapping all 8 city slugs to `(lat, lon)` — hardcoded is fine; coordinates should be city-centre approximations accurate to ~1 km
- [ ] `docker/migrations/002_hourly_weather.sql` updated: `hourly_weather` schema adds `city VARCHAR(64) NOT NULL`; primary key changed from `(date, hour)` to `(city, date, hour)`
- [ ] `fetch_weather_batch(dates, city="zurich")` accepts a `city` parameter; fetches from `CITY_COORDS[city]`; raises `ValueError` for unknown city slugs
- [ ] `persist_weather(df, city)` includes `city` in all INSERT rows and the `ON CONFLICT` clause targets `(city, date, hour)`
- [ ] `load_cached_dates(dates, city="zurich")` filters by `city` so cache misses are correctly detected per city
- [ ] Shared helper `_fetch_weather_for_df(df)` (in `ml/weather.py` or `ml/training_utils.py`) derives city per pool from `pool_metadata.json`, fetches weather per unique `(city, date)` pair (not per pool), and returns a combined DataFrame with a `city` column
- [ ] `scripts/train.py` and `ml/retrain.py` updated to call `_fetch_weather_for_df(df)` (or equivalent) so both use city-aware weather automatically
- [ ] Weather join in `ml/features.py` (and any other join sites) updated from `(date, hour)` to `(city, date, hour)` — pools without a known city fall back gracefully (log warning, drop weather columns for those rows rather than crashing)
- [ ] All existing weather-related tests updated to supply a `city` argument where required; no test may use `city="zurich"` as an implicit default to mask a missing city propagation
- [ ] All 8 new tests pass; no regression in existing test suite
- [ ] Manual smoke test: retrain with `python scripts/train.py`; verify DB contains `hourly_weather` rows for at least 2 distinct cities; verify training DataFrame has non-null weather features for a Bern pool

**Implementation Notes:**
Derive city from `pool_metadata.json` using the `city` field already present on each pool entry — no API changes needed. The `_fetch_weather_for_df` helper should group the training DataFrame by city, collect the union of dates per city, and issue one `fetch_weather_batch` call per city. Concatenate results into a single weather DataFrame with a `city` column before joining. The join key becomes `["city", "date", "hour"]`. For the `features.py` join, add `city` to the merge keys; pools with an unrecognised city slug should emit a warning and be trained without weather features (not dropped from training entirely). Keep `fetch_weather_batch` and its DB helpers fully backward-compatible via the `city="zurich"` default — any call sites outside the training pipeline (e.g., ad-hoc scripts) will continue to work without modification.

---

### TASK-027: Cache weekly "Beste Besuchszeiten" insights

**Phase:** 4  
**Status:** ✅ DONE  
**Dependencies:** TASK-013, TASK-015, TASK-016

**Description:**
The pool detail page (`/bad/{pool_uid}`) is slow because it computes a full 168-hour weekly prediction grid on every request to power the "Beste Besuchszeiten" (best visiting times) section. This involves 168 sequential XGBoost predictions, DB lag queries, and a weather fetch — work that produces a near-static insights dict that changes at most a few times per day.

This task introduces an in-memory weekly-insights cache keyed by `pool_uid`. Cache entries are refreshed in the background via `asyncio.create_task()` without blocking the response. Stale insights are served while a refresh is in flight; cold-cache requests return `None` (the template already handles this gracefully). The heavy 168-hour grid computation is thereby removed from the hot request path, reducing page load time to only the 24-hour today prediction.

**TDD — Write These Tests First:**
- `test_cache_hit_returns_cached_value`: seed `app.state.weekly_insights_cache` with a pre-computed entry whose `computed_at` is 60 seconds ago (TTL = 3600 s); assert `pool_detail()` returns the cached `weekly_insights` dict without calling `predict_range_batch` for the 168-hour slice
- `test_cache_miss_triggers_background_recompute`: call `pool_detail()` with an empty cache; assert `asyncio.create_task` is called once (background refresh kicked off), and the response returns `weekly_insights = None` (cold cache)
- `test_stale_cache_serves_old_value_while_refreshing`: seed cache with an entry whose `computed_at` exceeds the TTL; assert the response immediately returns the stale insights dict (does not block), and a background task is spawned for recomputation
- `test_ttl_expiry_logic`: unit-test the staleness check function `is_stale(computed_at, ttl)` for boundary conditions — exactly at TTL (stale), one second before TTL (fresh), far beyond TTL (stale)
- `test_background_refresh_updates_cache`: run the async refresh coroutine directly in a test; assert `app.state.weekly_insights_cache[pool_uid]` is updated with a new `computed_at` timestamp and a non-`None` insights dict after the coroutine completes
- `test_ttl_configurable_via_env_var`: set `WEEKLY_INSIGHTS_CACHE_TTL_SECONDS=120` in the test environment; assert the app reads this value and uses 120 s as the TTL instead of the default 3600 s
- `test_prewarm_populates_all_pools_at_startup` *(optional)*: mock the refresh coroutine; trigger the lifespan startup event; assert the refresh was scheduled for every pool uid in `pool_metadata.json`

**Acceptance Criteria:**
- [ ] `app.state.weekly_insights_cache` initialised as `dict[str, tuple[dict, datetime]]` (pool_uid → (insights_dict, computed_at)) during the FastAPI lifespan startup event
- [ ] `WEEKLY_INSIGHTS_CACHE_TTL_SECONDS` env var read at startup (default `3600`); surfaced in the existing app config/settings object
- [ ] `pool_detail()` in `api/main.py` checks the cache before computing the 168-hour weekly grid:
  - **Cache hit (fresh):** return cached `weekly_insights` directly — no 168-hour prediction call
  - **Cache hit (stale):** return cached `weekly_insights` immediately; spawn background task to recompute
  - **Cache miss (cold):** return `weekly_insights = None`; spawn background task to recompute
- [ ] Background refresh coroutine `_refresh_weekly_insights(pool_uid, db_pool)` is defined as a standalone `async def`; it calls `predict_range_batch` for the 168-hour window, runs `_compute_weekly_insights()`, and writes the result to `app.state.weekly_insights_cache[pool_uid]`
- [ ] Background task is launched with `asyncio.create_task(_refresh_weekly_insights(...))` — the request handler does **not** `await` it
- [ ] No more than one concurrent refresh per pool at a time — use a `set` of in-flight pool UIDs on `app.state` to guard against task pile-up under concurrent requests
- [ ] Staleness check extracted into a pure helper function `is_stale(computed_at: datetime, ttl: int) -> bool` — independently unit-testable
- [ ] Optional: pre-warm cache for all pools at startup (lifespan event schedules one background task per pool, non-blocking)
- [ ] All 6 required tests pass (test 7 optional); no regression in existing test suite
- [ ] Manual smoke test: load `/bad/<pool_uid>` twice in quick succession; second request logs a cache hit and completes visibly faster; `Beste Besuchszeiten` section renders correctly on both loads

**Implementation Notes:**
Store `weekly_insights_cache` and the in-flight guard set on `app.state` (FastAPI's built-in store for app-level mutable state) to avoid global variables and keep the cache accessible from tests via the test client's `app.state`. The background task must not silently swallow exceptions — wrap the coroutine body in `try/except Exception` and log errors at `WARNING` level including the pool UID. If the refresh fails, leave the existing cache entry intact (stale is better than nothing). Keep the 24-hour today prediction path (the fast path already on every page load) completely unchanged. This task is purely additive — the 168-hour computation moves from the hot path to the background; nothing else changes.

---

### TASK-028: Configurable time-bucket downsampling in data loader

**Phase:** 4  
**Status:** DONE  
**Dependencies:** TASK-009

**Description:**
The WebSocket collector records data at ~15–30 second granularity, producing ~2.5M rows for just 2 weeks of data across 31 pools. XGBoost training on the full dataset OOMs on the production server (Hetzner CX22, 4 GB RAM). Add configurable time-bucket downsampling to `load_data()` using TimescaleDB's `time_bucket()` function so training can target 5–10 minute averages (~50–100k rows), dramatically reducing memory pressure without meaningful loss of signal. Raw-query behaviour is preserved for backward compatibility.

**TDD — Write These Tests First:**
- `test_bucketed_query_uses_time_bucket`: mock/inspect the SQL generated when `bucket_interval="10 minutes"` — assert it contains `time_bucket` and `AVG(` substrings
- `test_raw_query_unchanged`: call `load_data(bucket_interval=None)` and assert the SQL does NOT contain `time_bucket`
- `test_empty_bucket_interval_falls_back_to_raw`: call `load_data(bucket_interval="")` and assert raw query path is used
- `test_env_var_picked_up`: set `TRAINING_BUCKET_INTERVAL="5 minutes"` in env before import; assert the default passed from `retrain.py` / `scripts/train.py` equals `"5 minutes"`
- `test_bucketed_record_count_plausible`: integration test against a seeded test DB — insert synthetic rows at 15-second intervals for 1 hour; after `load_data(bucket_interval="10 minutes")` assert row count ≤ 10 (6 buckets × 1 pool ± rounding) and > 0

**Acceptance Criteria:**
- [ ] `load_data()` in `ml/data_loader.py` accepts a new keyword argument `bucket_interval: str | None = "10 minutes"`
- [ ] When `bucket_interval` is a non-empty string: SQL uses `time_bucket($interval, time)` to group rows; selects `AVG(occupancy_pct)`, `AVG(current_fill)`, `AVG(free_space)`, and passthrough columns `pool_uid`, `pool_name`, `max_space`
- [ ] When `bucket_interval` is `None` or `""`: existing raw `SELECT *` query is used unchanged (no regression)
- [ ] `TRAINING_BUCKET_INTERVAL` env var is read by both `retrain.py` and `scripts/train.py`; its value (default `"10 minutes"`) is forwarded as the `bucket_interval` argument to `load_data()`
- [ ] Default of `"10 minutes"` is defined in one place (e.g. a constant in `ml/data_loader.py` or `ml/config.py`) — not duplicated across callers
- [ ] All 5 tests above pass; no regression in existing test suite
- [ ] A manual smoke test on the production server shows training completes without OOM with the default `"10 minutes"` bucket

**Implementation Notes:**
Use `psycopg2` / `asyncpg` parameter binding for the interval value to avoid SQL injection — `time_bucket(%s::interval, time)` with the interval passed as a query parameter, not f-string interpolation. The bucketed query should `ORDER BY pool_uid, bucket` for deterministic output. Returned column `time` (or `bucket`) must align with whatever the feature-engineering step (`ml/features.py`) expects as the timestamp column — rename `bucket` → `time` in the SELECT alias if needed. Consider adding a `--bucket-interval` CLI flag to `scripts/train.py` as an optional override over the env var (nice-to-have, not required for acceptance).

---

## Task Summary

| ID | Title | Phase | Status |
|---|---|---|---|
| TASK-001 | Project scaffold & tooling | 1 | ✅ DONE |
| TASK-002 | Docker Compose + TimescaleDB | 1 | ✅ DONE |
| TASK-003 | Database writer module | 1 | ✅ DONE |
| TASK-004 | WebSocket client module | 1 | ✅ DONE |
| TASK-005 | Collector main entry point | 1 | ✅ DONE |
| TASK-006 | Docker Compose — collector service | 1 | TODO |
| TASK-007 | Data validation & observability | 1 | ✅ DONE |
| TASK-008 | Feature engineering module | 2 | ✅ DONE |
| TASK-009 | Data loader (DB → DataFrame) | 2 | ✅ DONE |
| TASK-010 | Model training script | 2 | ✅ DONE |
| TASK-011 | Model evaluation & baseline | 2 | TODO |
| TASK-012 | Pool metadata file | 2 | ✅ DONE |
| TASK-013 | FastAPI app scaffold | 3 | ✅ DONE |
| TASK-014 | `/pools` endpoint | 3 | ✅ DONE |
| TASK-015 | `/predict` endpoint | 3 | ✅ DONE |
| TASK-016 | `/predict/range` endpoint | 3 | ✅ DONE |
| TASK-017 | Docker Compose — API service | 3 | TODO |
| TASK-018 | Automated retraining cron | 4 | ✅ DONE |
| TASK-019 | Weather feature integration | 4 | TODO |
| TASK-020 | Collector deduplication | 4 | ✅ DONE |
| TASK-021 | Integration test suite | 4 | TODO |
| TASK-022 | README & developer docs | 4 | ✅ DONE |
| TASK-023 | Persist weather data to TimescaleDB | 4 | ✅ DONE |
| TASK-024 | Fix automated retrainer to pass weather features | 4 | ✅ DONE |
| TASK-025 | Lightweight DB migration runner | 4 | ✅ DONE |
| TASK-026 | Per-city weather fetching | 4 | ✅ DONE |
| TASK-027 | Cache weekly "Beste Besuchszeiten" insights | 4 | ✅ DONE |
| TASK-028 | Configurable time-bucket downsampling in data loader | 4 | ✅ DONE |
