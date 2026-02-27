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
| TASK-018 | Automated retraining cron | 4 | TODO |
| TASK-019 | Weather feature integration | 4 | TODO |
| TASK-020 | Collector deduplication | 4 | TODO |
| TASK-021 | Integration test suite | 4 | TODO |
| TASK-022 | README & developer docs | 4 | ✅ DONE |
