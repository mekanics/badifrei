# Glossary — Opening Hours

| Term | Meaning |
|---|---|
| **Schedule** | The published claim: which intervals a pool is open, by date period and weekday. Slow-moving, lives in git. |
| **Interval** | One contiguous open span within a day, with a `condition` (`always` \| `fair_weather`). |
| **Period** | A date range plus weekday set, holding intervals. Replaces the flat `schedule` map. |
| **Closure** | A dated exception that overrides the schedule (Revision, event). `scope: full` closes the pool; `scope: partial` does not. |
| **Observation** | What the Baditicker feed reported at a timestamp. Asserts nothing about the future. Lives in TimescaleDB (`pool_status`). |
| **Resolution** | The derived answer for one pool at one instant: state, close times, reason, source. |
| **Confidence** | Provenance grade of a schedule: `official_structured`, `official_prose`, `unverified`. |
