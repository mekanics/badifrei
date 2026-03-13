"""Automated model retraining script with APScheduler."""
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import os

import pandas as pd

from ml.data_loader import load_data, InsufficientDataError
from ml.train import train, save_model
from ml.evaluate import evaluate
from ml.weather import fetch_weather_batch, CITY_COORDS

logger = logging.getLogger(__name__)

# Configurable via env
RETRAIN_INTERVAL_HOURS = int(os.getenv("RETRAIN_INTERVAL_HOURS", "168"))  # 7 days default
# 0 = use all available history (recommended); set to e.g. 365 to cap the window
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "90"))
MIN_RECORDS = int(os.getenv("MIN_RECORDS_FOR_TRAINING", "1000"))
MODELS_DIR = Path(__file__).parent / "models"


async def _fetch_weather_for_df(df: pd.DataFrame) -> "pd.DataFrame | None":
    """Fetch per-city weather for all unique (city, date) pairs in *df*.

    Derives city per pool from pool_metadata.json, then calls
    ``fetch_weather_batch(city=...)`` once per unique city — not once per pool.
    Returns a combined DataFrame with a ``city`` column, or ``None`` on failure.

    This shared helper is used by both :func:`retrain_job` and any other
    caller that needs city-aware weather aligned to a training DataFrame.
    Unknown pool UIDs default to "zurich" (with a warning) for backward compat.
    """
    try:
        from ml.features import load_pool_metadata
        metadata = load_pool_metadata()
        city_for_uid = {uid: meta.get("city", "zurich") for uid, meta in metadata.items()}

        dates_series = pd.to_datetime(df["time"]).dt.date
        temp = pd.DataFrame({"pool_uid": df["pool_uid"].values, "date": dates_series.values})
        temp["city"] = temp["pool_uid"].map(city_for_uid)

        unknown_mask = temp["city"].isna()
        if unknown_mask.any():
            unknown_uids = temp.loc[unknown_mask, "pool_uid"].unique().tolist()
            logger.warning(
                "pool_uid(s) %s not found in pool_metadata; defaulting to city='zurich'",
                unknown_uids,
            )
            temp["city"] = temp["city"].fillna("zurich")

        # One fetch per unique city — group unique (city, date) pairs
        city_date_pairs = temp[["city", "date"]].drop_duplicates()

        frames: list[pd.DataFrame] = []
        for city, group in city_date_pairs.groupby("city"):
            if city not in CITY_COORDS:
                logger.warning("City slug %r not in CITY_COORDS; skipping weather fetch", city)
                continue
            unique_dates = group["date"].unique()
            weather_df = await fetch_weather_batch(unique_dates, city=city)
            weather_df = weather_df.copy()
            weather_df["city"] = city
            frames.append(weather_df)

        if not frames:
            return pd.DataFrame(
                columns=["city", "date", "hour", "temperature_c", "precipitation_mm", "weathercode"]
            )

        combined = pd.concat(frames, ignore_index=True)
        logger.info(
            "Weather fetched for %d (city, date) pairs across %d city/cities",
            len(city_date_pairs),
            city_date_pairs["city"].nunique(),
        )
        return combined
    except Exception as exc:
        logger.warning(f"Weather fetch failed ({exc}); training without weather features")
        return None


async def retrain_job():
    """Run one retraining cycle."""
    window = f"last {LOOKBACK_DAYS}d" if LOOKBACK_DAYS > 0 else "all available history"
    logger.info(f"Starting retraining job (window: {window})...")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS) if LOOKBACK_DAYS > 0 else None

    try:
        df = await load_data(start, end, min_records=MIN_RECORDS)
    except InsufficientDataError as e:
        logger.warning(f"Skipping retrain: {e}")
        return

    logger.info(f"Loaded {len(df)} records for training")

    # Fetch weather features — graceful degradation on failure
    weather_df = await _fetch_weather_for_df(df)
    if weather_df is None:
        logger.warning("Training without weather features (fetch failed)")
    else:
        logger.info(f"Weather fetched for {len(pd.to_datetime(df['time']).dt.date.unique())} dates")

    from ml.train import time_based_split
    model, metrics = train(df, weather_df=weather_df)

    # Evaluate against baseline
    train_df, test_df = time_based_split(df, test_fraction=0.2)
    report = evaluate(model, train_df, test_df)

    logger.info(
        f"Model MAE: {report.model_mae:.2f}%  "
        f"Baseline MAE: {report.baseline_mae:.2f}%  "
        f"Beats baseline: {report.beats_baseline}"
    )

    path = save_model(model, metrics)
    logger.info(f"Model saved: {path}")

    # Prune models older than 30 days
    _prune_old_models()


def _prune_old_models(keep_days: int = 30):
    """Remove model files older than keep_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    pruned = 0
    for f in MODELS_DIR.glob("model_202*.ubj"):
        if f.is_symlink():
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            f.unlink()
            pruned += 1
            logger.info(f"Pruned old model: {f.name}")
    if pruned:
        logger.info(f"Pruned {pruned} old model(s)")


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        retrain_job,
        "interval",
        hours=RETRAIN_INTERVAL_HOURS,
        next_run_time=datetime.now(timezone.utc),  # Run immediately on startup
        id="retrain",
    )
    scheduler.start()
    logger.info(f"Retraining scheduler started (interval: {RETRAIN_INTERVAL_HOURS}h)")

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown(sig, frame):
        sig_name = signal.Signals(sig).name if isinstance(sig, int) else sig.name
        logger.info(f"Received {sig_name}, shutting down...")
        loop.call_soon_threadsafe(stop_event.set)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    await stop_event.wait()
    scheduler.shutdown()
    logger.info("Retrainer stopped.")


if __name__ == "__main__":
    asyncio.run(main())
