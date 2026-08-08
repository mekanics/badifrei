# ADR-003: Live, forecast-refresh, and archive weather upserts

**Date**: 2026-08-08
**Status**: Accepted (amended — archive training backfill; forecast TTL refresh)
**Deciders**: Eng review (live-weather-freshness, archive-training-weather)

## Context

`hourly_weather` is shared by ML training/features and the live API path that
builds `WeatherHint` for fair-weather hours and the detail-page air-temp chip.

Batch persist used `ON CONFLICT DO NOTHING`. That froze early **forecast** rows
forever:

1. **Live path:** the current UTC hour stuck at an old forecast (e.g. 21.5 °C /
   WMO 1 while Open-Meteo had ~29 °C / 0).
2. **Training path:** days first written inside the ~5-day forecast window never
   became archive actuals, so models trained on wrong weather (measured 1–4 °C
   / wrong codes vs archive).
3. **In-lag forecasts:** even before archive is available, Open-Meteo revises
   forecasts through the day; write-once rows stayed wrong until the 5-day lag.

## Options Considered

### Option 1: Flip batch persist to global `DO UPDATE`

Every `fetch_weather_batch` rewrite would overwrite all hours.

- Pros: One SQL path
- Cons: Forecast re-fetches could clobber archive-backed training truth

### Option 2: Separate live `current=` Open-Meteo path for UI only

- Pros: Best “right now” accuracy for the chip
- Cons: Fair-weather and training still poisoned; two weather models

### Option 3: Write policies by provenance (chosen)

| Path                  | Conflict policy                            | `source`   | TTL     |
| --------------------- | ------------------------------------------ | ---------- | ------- |
| Forecast batch upsert | `DO UPDATE` where source ∉ {archive, live} | `forecast` | 6 hours |
| Archive day refresh   | `DO UPDATE`                                | `archive`  | once    |
| Live current hour     | `DO UPDATE` + `fetched_at`                 | `live`     | 30 min  |

- Archive-eligible dates (`date < today - ARCHIVE_LAG_DAYS`, same cutoff as
  `_select_url`) with `source != 'archive'` (including legacy null) are
  re-fetched from the archive API and upserted (concurrency 1).
- In-lag dates re-fetch when `fetched_at` is null or older than
  `FORECAST_WEATHER_MAX_AGE` (6h).
- Forecast upserts never overwrite archive or live hours.
- Live `refresh_live_hour` keeps the chip / fair-weather path fresh.
  Request paths use stale-while-revalidate when a DB row exists (serve now,
  refresh in the background) so SSR and `/api/current` stay fast.

## Decision

Use Option 3. Never collapse into a single global `DO UPDATE`. Never treat a
stale forecast row as live truth or as final training truth once archive is
available. Do refresh in-lag forecasts on a multi-hour TTL because forecasts
drift.

## Consequences

- Migration `004_hourly_weather_fetched_at.sql` — nullable `fetched_at`
- Migration `005_hourly_weather_source.sql` — nullable `source`
- Null `fetched_at` ⇒ stale for live TTL and for in-lag forecast TTL
- Null / non-archive `source` on an archive-eligible day ⇒ archive refresh on
  next `fetch_weather_batch`
- Glossary: **Air-temp freshness**, **Forecast freshness**,
  **Archive-backed weather**
