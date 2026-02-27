"""Model evaluation and baseline comparison."""
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error


@dataclass
class PoolMetrics:
    pool_uid: str
    mae: float
    rmse: float
    n: int


@dataclass
class EvaluationReport:
    model_mae: float
    model_rmse: float
    baseline_mae: float
    baseline_rmse: float
    beats_baseline: bool
    per_pool: list[PoolMetrics]
    worst_pool: str  # pool_uid with highest MAE
    best_pool: str   # pool_uid with lowest MAE
    n_test: int


def naive_baseline_predict(df_train: pd.DataFrame, df_test: pd.DataFrame) -> np.ndarray:
    """
    Naive baseline: predict last week's occupancy at the same hour/weekday.

    For each row in df_test, find the most recent row in df_train with the
    same pool_uid, same hour_of_day, and same day_of_week.
    If not found, fall back to the pool's mean occupancy in train.
    """
    preds = []

    # Build lookup: (pool_uid, hour, weekday) -> most recent occupancy
    train_sorted = df_train.sort_values("time")
    lookup = {}
    for _, row in train_sorted.iterrows():
        key = (row["pool_uid"], row["time"].hour, row["time"].dayofweek)
        lookup[key] = row["occupancy_pct"]

    # Pool-level fallback means
    pool_means = df_train.groupby("pool_uid")["occupancy_pct"].mean().to_dict()
    global_mean = df_train["occupancy_pct"].mean()

    for _, row in df_test.iterrows():
        key = (row["pool_uid"], row["time"].hour, row["time"].dayofweek)
        if key in lookup:
            preds.append(lookup[key])
        elif row["pool_uid"] in pool_means:
            preds.append(pool_means[row["pool_uid"]])
        else:
            preds.append(global_mean)

    return np.array(preds)


def evaluate(
    model,
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
) -> EvaluationReport:
    """
    Evaluate model against naive baseline on test set.

    Args:
        model: fitted XGBRegressor
        df_train: training DataFrame (raw, with time + pool_uid + occupancy_pct)
        df_test: test DataFrame (raw)

    Returns EvaluationReport with per-pool breakdown.
    """
    from ml.features import build_features, FEATURE_COLUMNS
    from ml.train import prepare_xy

    # Build features for test set
    df_test_feat = build_features(df_test)
    X_test, y_test = prepare_xy(df_test_feat)

    # Model predictions
    model_preds = np.clip(model.predict(X_test), 0, 100)
    y_arr = y_test.values

    model_mae = float(mean_absolute_error(y_arr, model_preds))
    model_rmse = float(mean_squared_error(y_arr, model_preds) ** 0.5)

    # Baseline predictions
    baseline_preds = naive_baseline_predict(df_train, df_test)
    baseline_mae = float(mean_absolute_error(y_arr, baseline_preds))
    baseline_rmse = float(mean_squared_error(y_arr, baseline_preds) ** 0.5)

    # Per-pool metrics
    df_test_results = df_test.copy().reset_index(drop=True)
    df_test_results["model_pred"] = model_preds
    df_test_results["y_true"] = y_arr

    per_pool = []
    for uid, grp in df_test_results.groupby("pool_uid"):
        pool_mae = float(mean_absolute_error(grp["y_true"], grp["model_pred"]))
        pool_rmse = float(mean_squared_error(grp["y_true"], grp["model_pred"]) ** 0.5)
        per_pool.append(PoolMetrics(
            pool_uid=str(uid),
            mae=pool_mae,
            rmse=pool_rmse,
            n=len(grp),
        ))

    worst_pool = max(per_pool, key=lambda p: p.mae).pool_uid
    best_pool = min(per_pool, key=lambda p: p.mae).pool_uid

    return EvaluationReport(
        model_mae=model_mae,
        model_rmse=model_rmse,
        baseline_mae=baseline_mae,
        baseline_rmse=baseline_rmse,
        beats_baseline=model_mae < baseline_mae,
        per_pool=per_pool,
        worst_pool=worst_pool,
        best_pool=best_pool,
        n_test=len(df_test),
    )
