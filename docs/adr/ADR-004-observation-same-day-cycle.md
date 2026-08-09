# ADR-004: Observation same-day cycle gate

**Date**: 2026-08-09
**Status**: Proposed
**Deciders**: Eng review (observation-same-day-cycle), grill 2026-08-09

## Context

Baditicker often leaves `dateModified` stuck at the last status change. The
collector rewrites `observed_at` on every poll, so an overnight-stuck
`geschlossen` (e.g. Seebad Enge Friday close still in the feed on Sunday)
was treated as a fresh Observation and overrode a correct Schedule open.

Keying override eligibility only on `observed_at` correctly keeps same-day
afternoon weather-closes live when `dateModified` ages past 60 minutes. It
does not distinguish "confirmed closed today after doors" from "never flipped
back to offen after yesterday's close."

Demoting Observation when CrowdMonitor fill is non-zero was considered and
rejected: occupancy is a weak open/closed signal and would couple sensors into
hours Resolution.

## Options Considered

### Option 1: Same-day `source_modified_at` gate in `resolve`

Observation overrides Schedule only when `observed_at` is fresh **and**
`source_modified_at` is at or after today's first Guaranteed (`always`) open.
Applies to both open and closed. Missing timestamp or no Guaranteed open today
⇒ no override.

- Pros: Fixes overnight stuck closed/open; keeps afternoon sticky closes;
  stays inside `opening_hours`; no occupancy coupling
- Cons: Rare same-day pre-open weather close before first Guaranteed open can
  fall through to Schedule until Baditicker updates again

### Option 2: Demote Observation on occupancy conflict

Ignore `observed_closed` when live fill > 0 and Schedule says open.

- Pros: Would have fixed Enge while people were inside
- Cons: Quiet mornings look closed incorrectly; residual/staff fill when
  closed; wrong layer; does not fix stuck closed with zero fill

### Option 3: Calendar-day-only floor

Keep override only when `source_modified_at.date() == when.date()`.

- Pros: Stronger against pre-open same-day closes
- Cons: Slightly different semantics; grill chose Guaranteed-open threshold

## Decision

Adopt Option 1. Deepen Observation eligibility behind `resolve` via
`_observation_may_override`. Occupancy remains display-only.

## Consequences

- Glossary **Observation** documents dual eligibility (collector liveness +
  same-day cycle).
- Kill switch `OPENING_HOURS_USE_OBSERVED=0` unchanged.
- Water-temp freshness stays on its own dual gate (60m `observed_at` + 7d
  `source_modified_at`); do not conflate the two.
