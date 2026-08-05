# ADR-001: Guaranteed hours only in structured data

**Date**: 2026-08-05
**Status**: Accepted
**Deciders**: Product (CEO D1), SEO review, eng-review JSON-LD slice

## Context

Stadt Zürich Sommerbäder publish Guaranteed hours (typically morning) and
Conditional hours (afternoon fair weather). The site previously emitted
schema.org `openingHoursSpecification` from a flat optimistic close (e.g.
09:00–20:00 every day). That overstated when a pool is reliably open and
conflicted with CEO decision D1: express uncertainty, do not collapse it.

Search markup is crawled and may be shown to users who never open the page.
Misleading structured hours recreate the costly error (user expects open,
pool is weather-closed) at SERP scale.

## Options Considered

### Option 1: Optimistic full window in JSON-LD

Emit the latest fair-weather close as `closes` for every day.

- Pros: Longer hours look more attractive in local results; matches old metadata
- Cons: Materially false on unfair-weather afternoons; fights error asymmetry;
  disagrees with visible periods table

### Option 2: Guaranteed hours only in JSON-LD; Conditional hours in prose

Emit only `condition: always` intervals (plus full Closure closed windows).
Describe Conditional hours in FAQ and on-page copy.

- Pros: Honest; aligns with D1 and visible UI; valid schema.org without invented
  properties; Google special-hours pattern covers Revisions via 00:00:00
- Cons: SERP may show shorter hours (e.g. until 14:00); Conditional extension is
  not machine-encoded

### Option 3: Dual specs or invent weather-dependent properties

Try to encode weather in schema via non-standard fields or duplicate specs.

- Pros: Attempts full fidelity in machines
- Cons: Invalid / ignored by Google; high maintenance; false precision

## Decision

**Chosen**: Option 2 — Guaranteed hours only in Hours JSON-LD

Conditional hours remain first-class on the Schedule and in Resolution, UI, and
FAQ. Structured data asserts only what is true without a weather judgment.
Full Closures use `opens`/`closes` of `00:00:00` with `validFrom`/`validThrough`.

## Consequences

### Positive

- Schema, FAQ, and periods table can share one Schedule without lying
- Matches Stadt Zürich’s own guaranteed vs fair-weather split
- Reversible by changing the derived view; no migration

### Negative / Trade-offs

- Local results may understate afternoon availability in fair weather
- Closure vs open Period date overlap relies on Google’s special-hours
  interpretation (verify on Revision pages after ship)

## Implementation Notes

- Derive Hours JSON-LD from the Schedule module; do not re-read flat metadata
  closes for schema
- Times as `HH:MM:SS`; seasonal Periods carry `validFrom` / `validThrough`
- Do not emit a parallel `openingHours` string that re-claims the optimistic
  window
- Glossary: Guaranteed hours, Conditional hours, Hours JSON-LD
