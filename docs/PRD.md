# Product Requirements Document

**Project**: badifrei.ch (`badi-predictor`)
**Last Updated**: 2026-08-08
**Status**: Active

## Vision

Swiss public pools get crowded unpredictably. Live occupancy feeds exist, but
swimmers still cannot answer: _“Is it worth going to Käferberg at 3pm on
Saturday?”_ badifrei.ch collects that live data, forecasts occupancy with ML,
and presents it on a fast public site — so people can choose a quieter bath or
a better time.

## Target Users

- **Local swimmers** — Check live fill and today’s hourly forecast before
  leaving home; prefer a quieter pool or hour.
- **Occasional visitors / tourists** — Discover which baths are open and how
  busy they look without knowing the local Baditicker landscape.
- **Agents / search** — Consume honest structured data and markdown surfaces
  (`llms.txt`, `.md` pages) for answers that match what humans see.

## Goals

1. Show trustworthy live occupancy for covered baths (CrowdMonitor-backed).
2. Provide useful hourly occupancy forecasts for the rest of the day (and
   nearby days where the product already surfaces them).
3. Explain opening state honestly: Schedule, Closures, Conditional vs
   Guaranteed hours — see [glossary.md](./glossary.md) and
   [ADR-001](./adr/ADR-001-guaranteed-hours-in-structured-data.md).
4. Keep collection and retraining reliable with minimal ops (Compose / Coolify).

## Non-Goals

- User accounts, authentication, or personalized sync beyond local favorites
- Push notifications or mobile apps
- Admin UI for editing hours or models in production
- Anomaly detection product surface
- Full multi-city product expansion as a growth program (catalog already
  includes baths outside Zürich; Zürich remains the primary focus)
- Monetization or revenue generation

## Features

### Live occupancy

- Dashboard grouped by city with current fill / capacity and freshness
- Pool detail page with live status and capacity from CrowdMonitor max space

### Forecasts

- Hourly occupancy predictions per pool (XGBoost)
- Day-range predictions and history chart for context
- Weekly insights where the UI already computes them from prediction windows

### Opening hours and status

- Published Schedule from pool metadata (git-reviewed)
- Resolution of open / closed / conditional from schedule, Closures, and fresh
  Baditicker Observations
- Hours display on pool pages; Guaranteed hours only in Hours JSON-LD

### Discovery and SEO / LLM surfaces

- SSR pool pages under `/bad/{uid}` with structured data
- `/llms.txt`, homepage and pool markdown (`.md`), sitemap, robots

### Personalization (client-only)

- Favorites stored in browser `localStorage`

## Success Metrics

- User can open a pool page and see live occupancy plus a useful day forecast
- Forecast quality tracked via training reports (MAE vs naive baseline;
  stratified holdout metrics)
- Collector and API stay healthy under normal dashboard refresh traffic
- Structured hours do not overstate Conditional / fair-weather windows
  ([ADR-001](./adr/ADR-001-guaranteed-hours-in-structured-data.md))

## Constraints

- Public read-only service — no auth surface without explicit product approval
- Python 3.12+, `uv`, Docker Compose; deploy via Coolify with
  `scripts/migrate.py` ([COOLIFY.md](./COOLIFY.md))
- Jinja2 + vanilla JS frontend — no SPA framework unless explicitly requested
- Architecture authority: [SAD.md](./SAD.md); security baseline:
  [SECURITY_REVIEW.md](./SECURITY_REVIEW.md)

## Milestones (historical)

| Phase | Intent                       | Status    |
| ----- | ---------------------------- | --------- |
| 1     | Collection + TimescaleDB     | Delivered |
| 2     | First model + prediction API | Delivered |
| 3     | Weather features + retrain   | Delivered |
| —     | Public dashboard + SEO/LLM   | Delivered |
