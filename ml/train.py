"""XGBoost model training for pool occupancy prediction."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split

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


def prepare_xy(df: pd.DataFrame):
    """Extract feature matrix X and target y from DataFrame."""
    # Drop rows where lag features are NaN (first readings per pool)
    feature_df = df[FEATURE_COLUMNS].copy()
    target = df["occupancy_pct"].copy()

    # Fill NaN lag features with median, then 0 as fallback
    # (median is NaN if entire column is NaN, e.g. lag_1w with <7 days of data)
    feature_df = feature_df.fillna(feature_df.median()).fillna(0)

    return feature_df, target


def train(df: pd.DataFrame, test_fraction: float = 0.2) -> tuple[xgb.XGBRegressor, dict]:
    """
    Train XGBoost model on pool occupancy data.

    Returns (model, metrics_dict).
    """
    logger.info(f"Training on {len(df)} records")

    # Build stable encoding map from the full training dataset before building features
    encoding_map = get_pool_uid_encoding(df["pool_uid"].tolist())

    # Build features
    df_features = build_features(df, encoding_map=encoding_map)

    # Time-based split
    train_df, test_df = time_based_split(df_features, test_fraction)
    logger.info(f"Train: {len(train_df)} rows, Test: {len(test_df)} rows")

    X_train, y_train = prepare_xy(train_df)
    X_test, y_test = prepare_xy(test_df)

    # Train model
    model = xgb.XGBRegressor(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    # Evaluate
    preds = model.predict(X_test)
    preds = np.clip(preds, 0, 100)

    mae = mean_absolute_error(y_test, preds)
    rmse = mean_squared_error(y_test, preds) ** 0.5

    # Per-pool metrics
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
        "per_pool": per_pool,
        "feature_importance": dict(zip(FEATURE_COLUMNS, model.feature_importances_.tolist())),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }

    # Embed encoding map in metrics so save_model() can persist it as a sidecar
    metrics["pool_uid_encoding"] = {k: encoding_map[k] for k in sorted(encoding_map)}

    logger.info(f"MAE: {mae:.2f}%  RMSE: {rmse:.2f}%")
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
