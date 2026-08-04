#!/usr/bin/env python3
"""Report disagreement between Baditicker observations and computed schedules.

Run after Step 0 has collected observations. High disagreement on outdoor pools
means the weather predicate (Step 4) is valuable; low disagreement means the
schedule model (Step 3) is enough.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg

from ml.opening_hours import (
    load_schedules,
    observation_from_status_text,
    resolve,
)

ZURICH = ZoneInfo("Europe/Zurich")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        help="ISO date lower bound (default: all rows)",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set")
        return 1

    schedules = load_schedules()
    conn = await asyncpg.connect(db_url)
    try:
        if args.since:
            rows = await conn.fetch(
                """
                SELECT pool_uid, status_text, source_modified_at, observed_at
                FROM pool_status
                WHERE observed_at >= $1::timestamptz
                  AND status_text IS NOT NULL
                  AND status_text <> ''
                ORDER BY observed_at
                """,
                args.since,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT pool_uid, status_text, source_modified_at, observed_at
                FROM pool_status
                WHERE status_text IS NOT NULL AND status_text <> ''
                ORDER BY observed_at
                """
            )
    finally:
        await conn.close()

    if not rows:
        print("No observations with non-empty status_text yet.")
        return 0

    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"agree": 0, "disagree": 0, "n": 0}
    )

    for row in rows:
        uid = row["pool_uid"]
        schedule = schedules.get(uid)
        if schedule is None:
            continue
        when = row["observed_at"]
        if when.tzinfo is None:
            when = when.replace(tzinfo=ZURICH)
        else:
            when = when.astimezone(ZURICH)

        obs = observation_from_status_text(
            row["status_text"],
            observed_at=when,
            source_modified_at=row["source_modified_at"],
        )
        # Compare schedule-only resolution against the observation
        scheduled = resolve(schedule, when, observation=None)
        if obs.is_open is None:
            continue
        bucket = stats[uid]
        bucket["n"] += 1
        if scheduled.is_open == obs.is_open:
            bucket["agree"] += 1
        else:
            bucket["disagree"] += 1

    print(f"{'pool_uid':20s} {'n':>6s} {'agree':>6s} {'disagree':>8s} {'rate':>7s}")
    total_n = total_d = 0
    for uid in sorted(stats):
        s = stats[uid]
        rate = s["disagree"] / s["n"] if s["n"] else 0
        print(
            f"{uid:20s} {s['n']:6d} {s['agree']:6d} {s['disagree']:8d} {rate:7.1%}"
        )
        total_n += s["n"]
        total_d += s["disagree"]
    if total_n:
        print(f"{'TOTAL':20s} {total_n:6d} {total_n - total_d:6d} {total_d:8d} "
              f"{total_d / total_n:7.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
