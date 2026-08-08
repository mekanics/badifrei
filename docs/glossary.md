# badifrei — Glossary

> Definitions of the domain terms used across badifrei documentation and code.
> When in doubt, link here rather than redefine a term in place.
>
> Tag legend: **(CH)** Stadt Zürich / Swiss pool domain · **(seo)** structured-data /
> search presentation

## C

**Closure** _(CH)_ — A dated exception that overrides the Schedule (Revision,
event). Scope `full` closes the pool; `partial` does not change whole-pool open
state.

**Conditional hours** _(CH)_ _(seo)_ — Published Interval(s) that apply only
under fair weather (`condition: fair_weather`). Shown in UI and FAQ prose; never
asserted as unconditional open/close in structured data. Distinct from a
Resolution whose state is _open conditional_ (live, weather-resolved). See
[ADR-001](./adr/ADR-001-guaranteed-hours-in-structured-data.md).

**Confidence** — Provenance grade of a Schedule: `official_structured`,
`official_prose`, or `unverified`.

## G

**Guaranteed hours** _(CH)_ _(seo)_ — Published Interval(s) that apply regardless
of weather (`condition: always`). The only open windows asserted in Hours
JSON-LD. See [ADR-001](./adr/ADR-001-guaranteed-hours-in-structured-data.md).

## H

**Hours display view** — Derived UI projection of a Schedule for the pool detail
page. Not stored. Two shapes: seasonal periods (date ranges with nested weekday
hour groups) and weekday table (Mo–So cells that may hold multiple Intervals).
Built only from the Schedule — never from the legacy flat `pool_metadata` map
when a generated Schedule exists.

**Hours JSON-LD** _(seo)_ — The derived list of schema.org
`OpeningHoursSpecification` objects for one pool, built from Guaranteed hours
plus full Closures. Not a separate store of truth — always derived from the
Schedule. See [ADR-001](./adr/ADR-001-guaranteed-hours-in-structured-data.md).

## I

**Interval** — One contiguous open span within a day, classified as either
Guaranteed hours or Conditional hours.

## O

**Observation** — What the Baditicker feed reported at a timestamp. Asserts
nothing about the future; used only when fresh to override schedule-based
Resolution. A full Closure still outranks Observation (Revision cannot be
reopened by a lagging Baditicker `offen`).

## P

**Period** — A weekday set holding Intervals, optionally bounded by a date range.
Dated Periods model Sommerbad season sections; an evergreen Period (`start` and
`end` both unset) models year-round Hallenbad tables. The Schedule’s unit of
structure (replaces a single flat weekday map).

## R

**Resolution** — The derived answer for one pool at one instant: open state,
close times, reason, and source (observation, closure, or schedule).

## S

**Schedule** — The published claim: which Intervals a pool is open, by Period
and weekday, plus Closures. Slow-moving; reviewed in git before deploy.
