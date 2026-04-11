"""XGBoost model training for pool occupancy prediction."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error

from ml.features import build_features, get_pool_uid_encoding, FEATURE_COLUMNS

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent / "models"


def time_based_split(df: pd.DataFrame, test_fraction: float = 0.2):
    """
    Split by time — last test_fraction of data is test set.
    NEVER use random split for time series (data leakage!).
    """
    df = df.sort_values("time")
    split_idx = int(len(df) * (1 - test_fraction))
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def prepare_xy(
    df: pd.DataFrame,
    medians: "pd.Series | None" = None,
):
    """Extract feature matrix X and target y from DataFrame.

    Args:
        df: Feature-engineered DataFrame (must contain FEATURE_COLUMNS + occupancy_pct).
        medians: Pre-computed column medians (from training set) used to fill NaNs.
            When None, medians are computed from *df* itself -- only safe for training data.
    """
    feature_df = df[FEATURE_COLUMNS].copy()
    target = df["occupancy_pct"].copy()

    if medians is not None:
        feature_df = feature_df.fillna(medians).fillna(0)
    else:
        feature_df = feature_df.fillna(feature_df.median()).fillna(0)

    return feature_df, target


def train(
    df: pd.DataFrame,
    test_fraction: float = 0.2,
    weather_df: "pd.DataFrame | None" = None,
) -> tuple[xgb.XGBRegressor, dict]:
    """
    Train XGBoost model on pool occupancy data.

    Args:
        df: Raw occupancy DataFrame (time, pool_uid, occupancy_pct, …).
        test_fraction: Fraction of data (by time) held out for evaluation.
        weather_df: Optional combined weather DataFrame with columns
            [date, hour, temperature_c, precipitation_mm, weathercode].
            When provided, weather features are included in the model.
            Fetch with ``ml.weather.fetch_weather_batch`` before calling.

    Returns (model, metrics_dict).
    """
    logger.info(f"Training on {len(df)} records")

    # Build stable encoding map from the full training dataset before building features
    encoding_map = get_pool_uid_encoding(df["pool_uid"].tolist())

    # Build features (weather_df may be None — defaults will be used)
    df_features = build_features(df, encoding_map=encoding_map, weather_df=weather_df)

    # Time-based split
    train_df, test_df = time_based_split(df_features, test_fraction)
    logger.info(f"Train: {len(train_df)} rows, Test: {len(test_df)} rows")

    X_train, y_train = prepare_xy(train_df)
    train_medians = X_train.median()
    X_test, y_test = prepare_xy(test_df, medians=train_medians)

    model = xgb.XGBRegressor(
        n_estimators=500,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        tree_method="hist",
        n_jobs=2,
        early_stopping_rounds=20,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    preds = model.predict(X_test)
    preds = np.clip(preds, 0, 100)

    mae = mean_absolute_error(y_test, preds)
    rmse = mean_squared_error(y_test, preds) ** 0.5

    test_df = test_df.copy()
    test_df["pred"] = preds
    per_pool = {}
    for uid, group in test_df.groupby("pool_uid"):
        pool_preds = group["pred"].values
        pool_true = group["occupancy_pct"].values
        per_pool[uid] = {
            "mae": float(mean_absolute_error(pool_true, pool_preds)),
            "rmse": float(mean_squared_error(pool_true, pool_preds) ** 0.5),
            "n": len(group),
        }

    metrics = {
        "mae": float(mae),
        "rmse": float(rmse),
        "n_train": len(train_df),
        "n_test": len(test_df),
        "n_estimators_best": (
            int(model.best_iteration) + 1 if hasattr(model, "best_iteration") else 500
        ),
        "per_pool": per_pool,
        "feature_importance": dict(
            zip(FEATURE_COLUMNS, model.feature_importances_.tolist())
        ),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_medians": {
            k: float(v) if pd.notna(v) else 0.0 for k, v in train_medians.items()
        },
    }

    metrics["pool_uid_encoding"] = {k: encoding_map[k] for k in sorted(encoding_map)}

    logger.info(
        f"MAE: {mae:.2f}%  RMSE: {rmse:.2f}%  (best iteration: {metrics['n_estimators_best']})"
    )
    return model, metrics


def save_model(model: xgb.XGBRegressor, metrics: dict) -> Path:
    """Save model, encoding map sidecar, and training report to models/ directory."""
    MODELS_DIR.mkdir(exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    model_path = MODELS_DIR / f"model_{date_str}.ubj"
    latest_path = MODELS_DIR / "model_latest.ubj"
    encoding_path = MODELS_DIR / f"model_{date_str}.json"
    latest_encoding_path = MODELS_DIR / "model_latest.json"
    report_path = MODELS_DIR / "training_report.json"

    model.save_model(str(model_path))

    # Save uid encoding map sidecar (extracted from metrics, sorted keys for stability)
    encoding_map = metrics.get("pool_uid_encoding", {})
    with open(encoding_path, "w") as f:
        json.dump(encoding_map, f, indent=2)

    # Update symlinks
    if latest_path.exists() or latest_path.is_symlink():
        latest_path.unlink()
    latest_path.symlink_to(model_path.name)

    if latest_encoding_path.exists() or latest_encoding_path.is_symlink():
        latest_encoding_path.unlink()
    latest_encoding_path.symlink_to(encoding_path.name)

    # Save report (includes pool_uid_encoding for full auditability)
    with open(report_path, "w") as f:
        json.dump(metrics, f, indent=2)

    logger.info(f"Model saved: {model_path}")
    return model_path


def load_model(path: Path | None = None) -> xgb.XGBRegressor:
    """Load model from disk."""
    model_path = path or MODELS_DIR / "model_latest.ubj"
    model = xgb.XGBRegressor()
    model.load_model(str(model_path))
    return model
