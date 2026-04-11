"""Model evaluation and baseline comparison."""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

from ml.target_policy import clip_occupancy_target


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
    worst_pool: str
    best_pool: str
    n_test: int
    # e.g. peak_hours_mae, weekday_mae, weekend_mae, off_peak_mae
    stratified: dict[str, float] = field(default_factory=dict)


def naive_baseline_predict(df_train: pd.DataFrame, df_test: pd.DataFrame) -> np.ndarray:
    """Naive baseline: predict last week's occupancy at the same hour/weekday.

    Vectorized implementation — builds a lookup from training data keyed on
    (pool_uid, hour, dayofweek) then maps it onto the test set in one pass.
    Falls back to pool mean, then global mean, for missing keys.
    """
    train_times = pd.to_datetime(df_train["time"])
    train_lookup = df_train.copy()
    train_lookup["_hour"] = train_times.dt.hour
    train_lookup["_dow"] = train_times.dt.dayofweek
    train_lookup = train_lookup.sort_values("time")
    train_lookup["occupancy_pct"] = clip_occupancy_target(train_lookup["occupancy_pct"])
    # Keep last occurrence per (pool_uid, hour, dow) — the most recent
    lookup = train_lookup.drop_duplicates(
        subset=["pool_uid", "_hour", "_dow"], keep="last"
    ).set_index(["pool_uid", "_hour", "_dow"])["occupancy_pct"]

    pool_means = clip_occupancy_target(
        df_train.groupby("pool_uid")["occupancy_pct"].mean()
    )
    global_mean = float(clip_occupancy_target(float(df_train["occupancy_pct"].mean())))

    test_times = pd.to_datetime(df_test["time"])
    keys = pd.MultiIndex.from_arrays(
        [df_test["pool_uid"], test_times.dt.hour, test_times.dt.dayofweek],
        names=["pool_uid", "_hour", "_dow"],
    )
    preds = lookup.reindex(keys).values

    # Fallback 1: pool mean
    mask_nan = np.isnan(preds)
    if mask_nan.any():
        pool_fallback = df_test.loc[mask_nan, "pool_uid"].map(pool_means).values
        preds[mask_nan] = pool_fallback

    # Fallback 2: global mean
    mask_nan = np.isnan(preds)
    if mask_nan.any():
        preds[mask_nan] = global_mean

    return preds


def evaluate(
    model,
    df_train: pd.DataFrame,
    df_test: pd.DataFrame,
    encoding_map: "dict[str, int] | None" = None,
    weather_df: "pd.DataFrame | None" = None,
    train_medians: "pd.Series | None" = None,
) -> EvaluationReport:
    """Evaluate model against naive baseline on test set.

    Args:
        model: fitted XGBRegressor
        df_train: training DataFrame (raw, with time + pool_uid + occupancy_pct)
        df_test: test DataFrame (raw)
        encoding_map: pool_uid -> int mapping from training (prevents re-derivation)
        weather_df: weather DataFrame used during training (for consistent features)
        train_medians: column medians from training set (for NaN imputation)
    """
    from ml.features import build_features
    from ml.train import prepare_xy

    df_test_feat = build_features(
        df_test,
        encoding_map=encoding_map,
        weather_df=weather_df,
    )
    X_test, y_test = prepare_xy(df_test_feat, medians=train_medians)

    model_preds = np.clip(model.predict(X_test), 0, 100)
    y_arr = y_test.values

    model_mae = float(mean_absolute_error(y_arr, model_preds))
    model_rmse = float(mean_squared_error(y_arr, model_preds) ** 0.5)

    baseline_preds = np.clip(naive_baseline_predict(df_train, df_test_feat), 0.0, 100.0)
    baseline_mae = float(mean_absolute_error(y_arr, baseline_preds))
    baseline_rmse = float(mean_squared_error(y_arr, baseline_preds) ** 0.5)

    df_test_results = df_test_feat.copy().reset_index(drop=True)
    df_test_results["model_pred"] = model_preds
    df_test_results["y_true"] = y_arr

    stratified: dict[str, float] = {}
    hod = df_test_results["hour_of_day"].to_numpy()
    dow = df_test_results["day_of_week"].to_numpy()
    is_weekend = dow >= 5
    peak = (hod >= 9) & (hod <= 17)
    off_peak = ~peak
    if np.any(peak):
        stratified["peak_hours_mae"] = float(
            mean_absolute_error(y_arr[peak], model_preds[peak])
        )
    if np.any(off_peak):
        stratified["off_peak_hours_mae"] = float(
            mean_absolute_error(y_arr[off_peak], model_preds[off_peak])
        )
    wd = ~is_weekend
    we = is_weekend
    if np.any(wd):
        stratified["weekday_mae"] = float(
            mean_absolute_error(y_arr[wd], model_preds[wd])
        )
    if np.any(we):
        stratified["weekend_mae"] = float(
            mean_absolute_error(y_arr[we], model_preds[we])
        )

    per_pool = []
    for uid, grp in df_test_results.groupby("pool_uid"):
        pool_mae = float(mean_absolute_error(grp["y_true"], grp["model_pred"]))
        pool_rmse = float(mean_squared_error(grp["y_true"], grp["model_pred"]) ** 0.5)
        per_pool.append(
            PoolMetrics(
                pool_uid=str(uid),
                mae=pool_mae,
                rmse=pool_rmse,
                n=len(grp),
            )
        )

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
        stratified=stratified,
    )
