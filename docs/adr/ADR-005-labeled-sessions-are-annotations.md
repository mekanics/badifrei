# ADR-005: Labeled sessions are annotations

**Date**: 2026-09-01
**Status**: Proposed
**Deciders**: Product (hours display), eng (Schedule / scraper)

## Context

Stadt Zürich Hallenbad tables put more than one time range in a single cell.
The scraper used to emit every `HH–HH` match as an independent Guaranteed-hours
Interval and drop the prose. That produced three failures:

1. **Display**: Oerlikon Wednesday rendered `06:00–22:00 · 14:00–16:00` because
   Kinderspielnachmittag sat inside the public envelope.
2. **Correctness**: Käferberg / Bläsi weekend cells unioned mutually exclusive
   month variants (`9–16 Mai–September | 9–18 Oktober–April`), overstating
   summer hours in Resolution, ML `is_open`, and Hours JSON-LD.
3. **Coverage**: comma-separated day labels (`Samstag, Sonntag`) collapsed to
   Saturday, so Bläsi Sunday was closed.

A labeled fragment is sometimes redundant with an envelope (Oerlikon) and
sometimes the _only_ source of open time for its span (Bungertwies Wednesday
Kinderspiel; Käferberg restricted windows). Subtracting labeled Intervals from
Resolution would close those pools.

## Options Considered

### Option 1: Drop labeled fragments at scrape

Keep only the unlabeled envelope. Kinderspiel stays in `pool_metadata` notes.

- Pros: Smallest schema change; display becomes a single range
- Cons: Throws away published session names; Bungertwies / Käferberg lose
  actual open spans; month-conditional cells still need a separate fix

### Option 2: Display-only merge of overlapping windows

Leave generated Intervals as-is; absorb contained windows in the Hours display
view.

- Pros: No JSON schema change
- Cons: Month-conditional union still poisons Resolution, features, and JSON-LD;
  session names never surface

### Option 3: Interval.label + dated month-range Periods

Labeled fragments become Intervals with `label`. They remain additive in
Resolution, `resolve_frame`, `count_open_hours`, and Hours JSON-LD. The Hours
display view shows the union envelope plus Session lines. Month-range prose
becomes dated Periods, not labels.

- Pros: One scrape-site fix; display, forecasts, and SEO share the same
  Schedule; additive invariant preserves Bungertwies / Käferberg
- Cons: Mixed dated+evergreen Hallenbad schedules; wrapping ranges emit two
  Periods (Oktober–April → Oct–Dec and Jan–Apr)

## Decision

**Chosen**: Option 3 — labeled Intervals are Session annotations; they stay in
the open-time union.

Month-conditional fragments are not Sessions — they are date-bounded Periods.
Comma-separated weekday lists parse to every named day.

## Consequences

### Positive

- Oerlikon Wednesday shows `06:00–22:00` plus `14:00–16:00 Kinderspielnachmittag`
- Summer weekend close times match the published table (Käferberg Saturday
  16:00 May–September)
- Bläsi Sunday is open
- Session names survive scrape instead of living only in notes

### Negative / Trade-offs

- Year-wrapping month ranges become two same-year Periods, so the season note
  on a January Saturday reads `Januar–April` rather than `Oktober–April`.

## Implementation Notes

- Do not treat a Session as a closure or as a subtractive restriction
- `hours_display_view` and Hours JSON-LD merge overlapping same-condition
  windows for the envelope; gaps stay split. Sessions stay on the page, not
  in structured data.
- Mixed dated+evergreen Schedules use the weekday-table projection; Sommerbäder
  (dated Periods only) keep seasonal periods
- Rebuild generated hours from committed source fragments (`--from-sources`)
  so scrape changes are reviewable without the network

- Glossary: Session, Interval, Hours display view
