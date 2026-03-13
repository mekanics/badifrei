# PRD: Zürich Pool Occupancy Prediction System

**Version:** 1.0  
**Status:** Draft  
**Author:** Jarvis  
**Date:** 2026-02-27

---

## 1. Overview & Goals

### Problem

Zürich's public pools (Hallenbäder, Freibäder, Strandbäder) get crowded unpredictably. Swimmers show up to find packed pools, wasting a trip. There's live occupancy data available, but no way to answer: *"Is it worth going to Käferberg at 3pm on Saturday?"*

### Solution

A two-phase system:
1. **Collect** real-time occupancy data continuously and store it historically
2. **Predict** future occupancy per pool based on time patterns, day of week, and seasonality

### Success Criteria

- A user can query: *"What will the occupancy % at Wärmebad Käferberg be at 15:00 next Saturday?"* and get a useful prediction
- Predictions are reasonably accurate (target: MAE < 10% occupancy)
- Collection runs reliably 24/7 with minimal maintenance

---

## 2. Data Source

**WebSocket endpoint:** `wss://badi-public.crowdmonitor.ch:9591/api`

**Protocol:**
1. Connect via WS
2. Send message: `"all"`
3. Receive JSON array (push-based, real-time updates)

**Payload schema per pool:**
```json
{
  "uid": "string",         // Unique pool identifier
  "name": "string",        // Human-readable name (e.g. "Wärmebad Käferberg")
  "currentfill": 42,       // Current number of people
  "maxspace": 100,         // Capacity
  "freespace": 58          // Remaining capacity
}
```

**Coverage:** 22 pools across Zürich

**Derived field:** `occupancy_pct = currentfill / maxspace * 100`

---

## 3. Data Collection Service

### Architecture

A single long-running Python process that:
- Maintains a persistent WebSocket connection to the crowdmonitor API
- Receives push updates (no polling needed — it's push-based)
- Writes each update batch to the database with a timestamp
- Reconnects automatically on disconnect

### Storage Strategy

**Recommended DB: TimescaleDB** (PostgreSQL extension)

**Why not plain PostgreSQL?** Works fine, but time-series queries (e.g. "give me all readings for Käferberg on Saturdays between 14:00–16:00") get slow at scale without proper indexing. TimescaleDB adds hypertables and automatic partitioning on top of Postgres — zero new mental model, just `CREATE EXTENSION timescaledb`.

**Why not InfluxDB?** InfluxDB is great, but its query language (Flux) is unfamiliar and it's operationally heavier. TimescaleDB speaks SQL. KISS wins.

### Schema

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE pool_occupancy (
  time          TIMESTAMPTZ     NOT NULL,
  pool_uid      TEXT            NOT NULL,
  pool_name     TEXT            NOT NULL,
  current_fill  INTEGER         NOT NULL,
  max_space     INTEGER         NOT NULL,
  free_space    INTEGER         NOT NULL,
  occupancy_pct DOUBLE PRECISION GENERATED ALWAYS AS 
                (current_fill::float / NULLIF(max_space, 0) * 100) STORED
);

SELECT create_hypertable('pool_occupancy', 'time');
CREATE INDEX ON pool_occupancy (pool_uid, time DESC);
```

### Collection Service (Python)

```
collector/
  main.py          # Entry point, event loop
  ws_client.py     # WebSocket connection + reconnect logic
  db.py            # Database writer (asyncpg or psycopg3)
  config.py        # WS URL, DB URL from env vars
  requirements.txt
```

**Key libraries:**
- `websockets` — async WS client
- `asyncpg` — async Postgres driver (fast)
- `tenacity` — retry/reconnect logic

**Reconnect strategy:** Exponential backoff (1s → 2s → 4s → max 60s).

**Deployment:** Single Docker container. `restart: always`.

---

## 4. ML Prediction Approach

### Model Choice: Gradient Boosted Trees (XGBoost / LightGBM)

**Why?**
- Works well on tabular data with time features
- No need for data normalization
- Handles missing values gracefully
- Fast to train, fast to inference
- Interpretable (feature importance out of the box)
- No GPU needed

### Feature Engineering

| Feature | Source | Notes |
|---|---|---|
| `hour_of_day` | timestamp | 0–23 |
| `day_of_week` | timestamp | 0=Mon, 6=Sun |
| `is_weekend` | timestamp | bool |
| `month` | timestamp | 1–12 (seasonality) |
| `is_holiday` | calendar | Swiss/Zürich public holidays |
| `pool_uid` | data | encoded as integer (label encode) |
| `pool_type` | static config | Hallenbad / Freibad / Strandbad |
| `lag_1h` | historical | Occupancy 1 hour ago at same pool |
| `lag_1w` | historical | Occupancy same time last week |
| `rolling_mean_7d` | historical | 7-day rolling avg at same hour/dow |

**Weather features (Phase 2, optional):** Temperature and sunshine hours from Open-Meteo (free, no API key).

**Minimum data needed before training:** ~4 weeks for a usable model. ~3 months for good seasonal signal.

### Training Pipeline

```
ml/
  features.py      # Feature extraction from DB
  train.py         # XGBoost training + model save
  evaluate.py      # MAE, RMSE, per-pool breakdown
  predict.py       # Load model, generate predictions
  models/          # Saved model artifacts (.pkl or .ubj)
```

**Retraining schedule:** Weekly cron job.

**Target variable:** `occupancy_pct` (0–100, regression)

---

## 5. Prediction API

### Simple FastAPI service

**Endpoints:**

```
GET /pools                                        # List all pools
GET /predict?pool_uid={uid}&datetime={ISO8601}    # Single prediction
GET /predict/range?pool_uid={uid}&date={date}     # Full day (hourly)
```

**Response example:**
```json
{
  "pool_uid": "SSD-5",
  "pool_name": "Wärmebad Käferberg",
  "predicted_at": "2026-03-07T15:00:00+01:00",
  "predicted_occupancy_pct": 73.4,
  "confidence": "medium",
  "model_version": "2026-02-24"
}
```

---

## 6. Tech Stack

| Component | Choice | Rationale |
|---|---|---|
| **Language** | Python 3.12 | Best ML ecosystem, asyncio for WS collection |
| **Database** | TimescaleDB (on Postgres) | SQL familiarity + time-series performance |
| **WS client** | `websockets` + `asyncio` | Lightweight, async-native |
| **DB driver** | `asyncpg` | Fastest async Postgres driver |
| **ML model** | XGBoost | KISS, accurate, handles tabular data well |
| **API framework** | FastAPI | Modern, fast, auto-docs |
| **Containerization** | Docker Compose | Collector + API + DB in one compose file |
| **Scheduling** | `cron` or `APScheduler` | Weekly model retrain |

---

## 7. Milestones

### Phase 1 — Data Collection (Week 1–2)
- [ ] Set up TimescaleDB + schema
- [ ] Build WebSocket collector with reconnect logic
- [ ] Deploy via Docker Compose
- [ ] Verify data flowing and persisting correctly
- [ ] Add basic alerting if collector dies

**Exit criteria:** 2+ weeks of clean, continuous data

### Phase 2 — First Model (Week 4–6)
- [ ] Feature engineering pipeline
- [ ] Train initial XGBoost model
- [ ] Evaluate per-pool MAE/RMSE
- [ ] Build FastAPI prediction endpoint

**Exit criteria:** Working `/predict` endpoint, MAE < 15%

### Phase 3 — Polish (Week 6–8)
- [ ] Add weather features (Open-Meteo)
- [ ] Weekly automated retraining cron
- [ ] Model versioning + rollback

**Exit criteria:** MAE < 10%, system self-maintaining

---

## 8. Out of Scope

- User accounts / auth
- Push notifications
- Real-time dashboard
- Mobile app
- Multi-city support
- Anomaly detection
- Admin UI

---

## 9. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| WS API goes down / changes | Medium | Reconnect logic + alert on extended outage |
| Insufficient data | Low (time fixes it) | Start collecting immediately |
| Pool closures / seasonal gaps | High (Freibäder close in winter) | Flag pool type; train per pool type |
| API schema changes | Low | Schema validation on ingest; dead-letter log |
