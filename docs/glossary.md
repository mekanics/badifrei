# badifrei — Glossary

> Definitions of the domain, technical, and product terms used across badifrei
> documentation and code. When in doubt, link here rather than redefine a term
> in place.
>
> Tag legend: **(CH)** Stadt Zürich / Swiss pool domain · **(seo)** structured-data /
> search presentation

## A — C

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

**CrowdMonitor max space** _(seo)_ — Live sensor capacity from the CrowdMonitor
feed (`pool_occupancy.max_space`). Source of truth for live fill percentage
(`occupancy_pct = current_fill / max_space`), the guest-count badge denominator,
and SEO/FAQ capacity (`maximumAttendeeCapacity`) via the latest occupancy row.
Not stored in `pool_metadata.json`.

## G — L

**Guaranteed hours** _(CH)_ _(seo)_ — Published Interval(s) that apply regardless
of weather (`condition: always`). The only open windows asserted in Hours
JSON-LD. See [ADR-001](./adr/ADR-001-guaranteed-hours-in-structured-data.md).

**Hours display view** — Derived UI projection of a Schedule for the pool detail
page. Not stored. Two shapes: seasonal periods (date ranges with nested weekday
time groups) and weekday table (Mo–So cells that may hold multiple Intervals).
Built only from the Schedule — never from the legacy flat `pool_metadata` map
when a generated Schedule exists.

**Hours JSON-LD** _(seo)_ — The derived list of schema.org
`OpeningHoursSpecification` objects for one pool, built from Guaranteed hours
plus full Closures. Not a separate store of truth — always derived from the
Schedule. See [ADR-001](./adr/ADR-001-guaranteed-hours-in-structured-data.md).

**Interval** — One contiguous open span within a day, classified as either
Guaranteed hours or Conditional hours.

## M — R

**Observation** — What the Baditicker feed reported at a timestamp. Asserts
nothing about the future; used only when fresh to override schedule-based
Resolution. A full Closure still outranks Observation (Revision cannot be
reopened by a lagging Baditicker `offen`).

**Period** — A weekday set holding Intervals, optionally bounded by a date range.
Dated Periods model Sommerbad season sections; an evergreen Period (`start` and
`end` both unset) models year-round Hallenbad tables. The Schedule’s unit of
structure (replaces a single flat weekday map).

**Resolution** — The derived answer for one pool at one instant: open state,
close times, reason, and source (observation, closure, or schedule).

## S — Z

**Schedule** — The published claim: which Intervals a pool is open, by Period
and weekday, plus Closures. Slow-moving; reviewed in git before deploy.

**Wassertemperatur (water temperature)** _(CH)_ — Per-pool water temperature from
the Baditicker feed (`pool_status.water_temp_c`). Available for outdoor pools
only; all `hallenbad` entries publish it empty. Displayed only when both
water-temp freshness gates pass. Distinct from **air temperature**, which is
city-level from `hourly_weather` and identical across all pools in a city.

**Water-temp freshness gates** — `observed_at` within 60 minutes (collector
alive) **and** `source_modified_at` within 7 days (city still maintaining).
Deliberately not a single short `source_modified_at` cutoff: measured Baditicker
update gaps reach 23.4 h in peak season.
