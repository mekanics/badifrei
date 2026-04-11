"""Optional walk-forward MAE — many full retrains; run manually when needed.

Usage (from repo root, with DATABASE_URL set)::

    uv run python -m scripts.walk_forward
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

from ml.data_loader import load_data, DEFAULT_BUCKET_INTERVAL
from ml.retrain import _fetch_weather_for_df
from ml.walk_forward import walk_forward_fold_maes


async def main():
    days = int(os.getenv("LOOKBACK_DAYS", "90"))
    min_rec = int(os.getenv("MIN_RECORDS", "2000"))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bucket = os.getenv("TRAINING_BUCKET_INTERVAL", DEFAULT_BUCKET_INTERVAL)

    print(f"Loading up to {days}d of data (min {min_rec} rows)...")
    df = await load_data(start, end, min_records=min_rec, bucket_interval=bucket)
    print(f"Loaded {len(df)} rows")

    weather_df = await _fetch_weather_for_df(df)
    n_folds = int(os.getenv("WALK_FORWARD_FOLDS", "3"))
    print(
        f"Running walk_forward_fold_maes (n_folds={n_folds}) — this may take a while..."
    )
    out = walk_forward_fold_maes(df, weather_df=weather_df, n_folds=n_folds)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
