"""Back-test script: verify end-to-end prediction pipeline against real DB data.

Usage:
    python -m scripts.backtest
    # or from project root:
    DATABASE_URL=postgres://... python scripts/backtest.py

The script:
  1. Finds a pool that has ≥7 days of data.
  2. For each of the last 7 days, calls predict_range_batch() for that day.
  3. Compares hourly predictions against actual recorded occupancy.
  4. Reports per-day and overall RMSE / MAE.
"""
import asyncio
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Make sure project root is on sys.path when run directly
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncpg  # noqa: E402
from api.predictor import Predictor  # noqa: E402
from ml.features import FEATURE_COLUMNS  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def rmse(errors: list[float]) -> float:
    return math.sqrt(sum(e ** 2 for e in errors) / len(errors)) if errors else float("nan")


def mae(errors: list[float]) -> float:
    return sum(abs(e) for e in errors) / len(errors) if errors else float("nan")


async def find_eligible_pool(db: asyncpg.Connection, min_days: int = 7) -> str | None:
    """Return a pool_uid with ≥ min_days of distinct calendar days of data."""
    rows = await db.fetch(
        """
        SELECT pool_uid, COUNT(DISTINCT DATE(time AT TIME ZONE 'UTC')) AS day_count
        FROM pool_occupancy
        GROUP BY pool_uid
        HAVING COUNT(DISTINCT DATE(time AT TIME ZONE 'UTC')) >= $1
        ORDER BY day_count DESC
        LIMIT 1
        """,
        min_days,
    )
    return rows[0]["pool_uid"] if rows else None


async def fetch_actuals(
    db: asyncpg.Connection,
    pool_uid: str,
    day: date,
) -> dict[int, float]:
    """Return {hour: avg_occupancy_pct} for the given pool and day."""
    rows = await db.fetch(
        """
        SELECT
            EXTRACT(HOUR FROM time AT TIME ZONE 'UTC')::int AS hour,
            AVG(occupancy_pct) AS occupancy_pct
        FROM pool_occupancy
        WHERE pool_uid = $1
          AND time >= $2
          AND time <  $2 + INTERVAL '1 day'
        GROUP BY hour
        ORDER BY hour
        """,
        pool_uid,
        datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
    )
    return {int(r["hour"]): float(r["occupancy_pct"]) for r in rows}


# ── main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set.")
        sys.exit(1)

    # Load model
    predictor = Predictor()
    ok = predictor.load()
    if not ok:
        print("WARNING: No model loaded. Predictions will be 0.0 — "
              "back-test will still exercise the pipeline.")
    else:
        print(f"Model loaded: {predictor.model_version}")
        print(f"Feature columns ({len(FEATURE_COLUMNS)}): {FEATURE_COLUMNS}\n")

    db_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)

    try:
        async with db_pool.acquire() as conn:
            pool_uid = await find_eligible_pool(conn, min_days=7)

        if pool_uid is None:
            print("No pool with ≥7 days of data found. Collect more data and retry.")
            return

        print(f"Back-testing pool: {pool_uid}\n")

        today = date.today()
        test_days = [today - timedelta(days=i) for i in range(7, 0, -1)]  # oldest → newest

        all_errors: list[float] = []

        for day in test_days:
            # Build the 24 UTC hours for this day
            hours = [
                datetime(day.year, day.month, day.day, h, 0, 0, tzinfo=timezone.utc)
                for h in range(24)
            ]

            # Run predict_range_batch (includes weather + rolling_mean fetch)
            preds = await predictor.predict_range_batch(pool_uid, hours, db_pool)

            # Fetch actuals from DB
            async with db_pool.acquire() as conn:
                actuals = await fetch_actuals(conn, pool_uid, day)

            # Compare — only hours where we have actual data
            day_errors: list[float] = []
            for h, pred in enumerate(preds):
                actual = actuals.get(h)
                if actual is not None:
                    day_errors.append(pred - actual)

            all_errors.extend(day_errors)

            if day_errors:
                print(
                    f"  {day}  hours with data: {len(day_errors):2d}  "
                    f"MAE: {mae(day_errors):5.1f}%  RMSE: {rmse(day_errors):5.1f}%"
                )
            else:
                print(f"  {day}  no actual data recorded")

        if all_errors:
            print(f"\n{'─' * 55}")
            print(f"  Overall ({len(all_errors)} hours)  "
                  f"MAE: {mae(all_errors):.2f}%  RMSE: {rmse(all_errors):.2f}%")
        else:
            print("\nNo overlapping predicted/actual data — cannot compute metrics.")

    finally:
        await db_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
