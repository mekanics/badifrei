"""Training script — run via `make train`."""
import asyncio
import os
from datetime import datetime, timezone, timedelta

from ml.data_loader import load_data
from ml.train import train, save_model, time_based_split
from ml.evaluate import evaluate


async def main():
    days = int(os.getenv("LOOKBACK_DAYS", "90"))
    min_rec = int(os.getenv("MIN_RECORDS", "100"))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    print(f"Loading data ({days}d lookback, min {min_rec} records)...")
    df = await load_data(start, end, min_records=min_rec)
    print(f"Loaded {len(df)} records for {df['pool_uid'].nunique()} pools")

    # Fetch per-city weather for all (city, date) pairs in the training set.
    print("Fetching weather data for training dates (city-aware)...")
    try:
        from ml.retrain import _fetch_weather_for_df
        weather_df = await _fetch_weather_for_df(df)
        if weather_df is not None:
            n_cities = weather_df["city"].nunique() if "city" in weather_df.columns else 1
            print(f"  Weather fetched: {len(weather_df)} rows across {n_cities} city/cities")
        else:
            print("  Weather fetch failed; training without weather features")
    except Exception as exc:
        print(f"  Weather fetch failed ({exc}); training without weather features")
        weather_df = None

    model, metrics = train(df, weather_df=weather_df)

    train_df, test_df = time_based_split(df, test_fraction=0.2)
    report = evaluate(model, train_df, test_df)

    path = save_model(model, metrics)
    print(f"Model saved: {path}")
    print(f"MAE:  {report.model_mae:.1f}%  (baseline: {report.baseline_mae:.1f}%)")
    print(f"RMSE: {report.model_rmse:.1f}%")
    print(f"Beats baseline: {report.beats_baseline}")
    print(f"Worst pool: {report.worst_pool}  ({max(p.mae for p in report.per_pool):.1f}% MAE)")


if __name__ == "__main__":
    asyncio.run(main())
