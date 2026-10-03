# ADR-006: Schedule goes live from git at runtime, not by deploy

**Date**: 2026-10-03
**Status**: Proposed
**Deciders**: Product (hours delivery), eng

## Context

The hours-sync GitHub Action opens a PR when Stadt Zürich pages change. Merging
that PR rebuilt every image (`COPY ml/`) and Coolify Compose recreated `api`,
`collector`, and `retrain`. Compose apps have no rolling update, so users saw
an outage even when only JSON changed. Most sync PRs since 2026-09-01 were
timestamp-only.

Published hours stay git-reviewed (PRD / SAD). They must reach the running api
without a container restart.

## Options Considered

### Option 1: Runtime fetch from `main`

The api GETs the generated JSON from public `raw.githubusercontent.com` every
~15 minutes, parses it with the same lenient loader as today, and swaps the
in-memory snapshot. Coolify Watch Paths skip `ml/data/**`.

- Pros: No deploy for data; same review gate; rollback is a revert; off unless
  `HOURS_SYNC_URL` is set
- Cons: Adds one outbound GET; ~20 min lag (CDN + poll); a bad web-editor edit
  can reach production within that window (same parser as a deploy)

### Option 2: Database table as source of truth

- Pros: No GitHub dependency at serve time
- Cons: New write path and credentials on a public read-only service; contradicts
  SAD ("Published hours live in git … not in the DB")

### Option 3: Online table (Sheets / Airtable) as source or override

- Pros: Easier manual edits
- Cons: Second parser, third-party runtime dependency, silent stale rows;
  demand is near zero (no manual hours edits since September)

### Option 4: Night-time bot auto-merge

- Pros: Tiny workflow change
- Cons: Real hours changes skip human review; still a deploy and outage, just
  at 03:00

### Option 5: Accept deploys for real changes; only stop timestamp churn

- Pros: Smallest code change
- Cons: Season transitions still take the site down

## Decision

**Chosen**: Option 1 — runtime fetch from `main`

Scrapers already write only on content change (so no-op runs open no PR).
Real hours changes stay PR-reviewed. The running api pulls the merged file.
Retrain keeps the baked file until the next code deploy.

## Consequences

### Positive

- Data-only merges no longer recreate containers
- Hours are live within ~20 minutes of merge
- Local and unit tests stay offline when `HOURS_SYNC_URL` is empty

### Negative / Trade-offs

- Production depends on GitHub raw being reachable; last good / baked cover outages
- Watch Paths must be set only after the poller is live
- Blanking `HOURS_SYNC_URL` in Coolify is not a kill switch (`:-` default)

## Implementation Notes

- Module: `ml/schedule_source.py`. Seams: `api.prediction_days._schedules()` and
  `ml.features.add_opening_hours_features(df, None)`.
- Structural guards only (status, size, JSON shape). Per-pool parse stays lenient.
- `/health` exposes hours status and stays HTTP 200 regardless of fetch result.
