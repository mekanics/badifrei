# 🏊 badifrei.ch

> Ist deine Lieblingsbadi gerade voll? Schau nach.

Live Auslastung und KI-Prognosen für Zürcher Bäder — powered by real-time WebSocket data, XGBoost ML, and TimescaleDB.

---

## Was ist das?

**badifrei.ch** zeigt die aktuelle Auslastung aller öffentlichen Bäder in Zürich (und einigen weiteren Städten) — live, jede Minute aktualisiert. Dazu kommen stündliche Prognosen für den Rest des Tages, basierend auf einem Machine-Learning-Modell.

**Features:**

- 🟢 Live-Auslastung via CrowdMonitor WebSocket-Feed
- 🤖 XGBoost-Prognosemodell (MAE ~0.3%, wöchentlich retrained)
- 📅 Historische Auslastung im Tagesverlauf (Diagramm)
- 🕐 Öffnungszeiten pro Bad
- ⭐ Favoriten (localStorage)
- 🏙️ Gruppierung nach Stadt

---

## Stack

| Komponente    | Technologie                          |
| ------------- | ------------------------------------ |
| Datensammlung | Python + asyncio + websockets        |
| Datenbank     | TimescaleDB (PostgreSQL)             |
| ML Pipeline   | XGBoost, pandas, scikit-learn        |
| API           | FastAPI + asyncpg                    |
| Frontend      | Jinja2 + Chart.js (kein Framework)   |
| Wetter        | Open-Meteo API (kostenlos, kein Key) |
| Deployment    | Docker Compose                       |

---

## Architektur

```
┌─────────────────┐     WebSocket      ┌──────────────────────┐
│  CrowdMonitor   │ ─────────────────► │  collector           │
│  (badi-info.ch) │                    │  (asyncio writer)    │
└─────────────────┘                    └──────────┬───────────┘
                                                  │
                                            TimescaleDB
                                                  │
                              ┌───────────────────┼──────────────────┐
                              │                   │                  │
                        ┌─────▼─────┐     ┌───────▼──────┐  ┌───────▼──────┐
                        │  api      │     │  retrain     │  │  (future)    │
                        │  FastAPI  │     │  APScheduler │  │              │
                        └─────┬─────┘     └──────────────┘  └──────────────┘
                              │
                        ┌─────▼─────┐
                        │  Browser  │
                        │  Dashboard│
                        └───────────┘
```

---

## Lokales Setup

### Voraussetzungen

- Docker + Docker Compose
- Python 3.12 + `uv`

### Starten

```bash
# 1. Env-Datei anlegen
cp .env.example .env
# .env anpassen (DB-Passwort etc.)

# 2. Stack starten
docker compose up -d

# 3. Dashboard aufrufen
open http://localhost:8000
```

### Modell trainieren

```bash
# Innerhalb Docker (empfohlen):
docker compose run --rm retrain

# Oder lokal (braucht laufende DB):
make train
```

### Tests

```bash
make test              # Unit-Tests (kein DB nötig)
make test-integration  # Integration-Tests (braucht DB)
```

---

## ML-Modell

Das Modell prognostiziert die Auslastung (0–100%) für jedes Bad, für jede Stunde des Tages.

**Features:**

- Tageszeit (`hour_of_day`), Wochentag, Monat
- Öffnungsstatus (`is_open`, `minutes_since_open`, `minutes_until_close`)
- Wetter (Temperatur, Niederschlag, Sonnenstunden via Open-Meteo)
- Letzte bekannte Auslastung (`lag_1h`, gleitende Mittelwerte)
- Pool-ID (encoded)

**Performance (Stand Feb 2026):**

- 108'000+ Datenpunkte, 31 Bäder
- MAE: **0.33%** vs. Baseline 16.38%

---

## Datenquellen

| Quelle                                                                                                   | Was                                             |
| -------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| [badi-info.ch](https://badi-info.ch) / CrowdMonitor                                                      | Live-Auslastung via WebSocket                   |
| [Stadt Zürich](https://www.stadt-zuerich.ch/de/stadtleben/sport-und-erholung/sport-und-badeanlagen.html) | Öffnungszeiten                                  |
| [Open-Meteo](https://open-meteo.com)                                                                     | Wetterprognosen (kostenlos, kein API-Key nötig) |

---

## Umgebungsvariablen

Siehe `.env.example` für alle verfügbaren Variablen.

| Variable                   | Beschreibung                         | Default                               |
| -------------------------- | ------------------------------------ | ------------------------------------- |
| `DATABASE_URL`             | PostgreSQL-Verbindung                | `postgresql://badi:badi@db:5432/badi` |
| `RETRAIN_INTERVAL_HOURS`   | Trainingsintervall in Stunden        | `168` (7 Tage)                        |
| `MIN_RECORDS_FOR_TRAINING` | Mindestanzahl Datenpunkte            | `1000`                                |
| `LOOKBACK_DAYS`            | Trainings-Zeitfenster                | `90`                                  |
| `UMAMI_SCRIPT_URL`         | URL zum Umami `script.js` (optional) | _(leer)_                              |
| `UMAMI_WEBSITE_ID`         | Website-ID aus dem Umami-Dashboard   | _(leer)_                              |

---

## Projektstruktur

```
badifrei/
├── api/                  # FastAPI-App + Templates
│   ├── main.py
│   ├── templates/        # Jinja2 HTML
│   └── static/           # CSS
├── collector/            # WebSocket-Collector
│   ├── collector.py
│   └── config.py
├── ml/                   # Machine Learning
│   ├── features.py       # Feature Engineering
│   ├── train.py          # Training
│   ├── predictor.py      # Inference
│   ├── retrain.py        # Scheduler
│   ├── weather.py        # Open-Meteo Integration
│   └── pool_metadata.json
├── docker/
│   └── init.sql          # TimescaleDB Schema
├── tests/
│   ├── unit/
│   └── integration/
└── docker-compose.yml
```

---

## Entwickelt mit

☕ Kaffee + 🤖 KI (Claude) — orchestriert von [j2y.dev](https://j2y.dev)
