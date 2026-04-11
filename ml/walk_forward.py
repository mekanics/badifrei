"""Expanding-window walk-forward evaluation (offline / optional).

Each fold retrains an XGBoost model on an **expanding** prefix of *df* and
scores MAE on the next time slice.  This is **slow** (``n_folds`` full
``train()`` calls) and is **not** run in the production retrain job — use for
calibration studies or before major pipeline changes.

Example::

    from ml.walk_forward import walk_forward_fold_maes
    maes = walk_forward_fold_maes(df_raw, weather_df=weather_df, n_folds=3)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass


def walk_forward_fold_maes(
    df: pd.DataFrame,
    *,
    weather_df: "pd.DataFrame | None" = None,
    n_folds: int = 3,
    test_fraction: float = 0.08,
    min_train_fraction: float = 0.45,
) -> dict:
    """Return per-fold holdout MAE and mean MAE using an expanding train window.

    Args:
        df: Raw occupancy rows (``time``, ``pool_uid``, ``occupancy_pct``, …).
        weather_df: Optional combined weather frame passed through to ``train``.
        n_folds: Number of evaluation folds after the initial train cut.
        test_fraction: Fraction of *total* rows per test slice (minimum 1 row).
        min_train_fraction: Initial train fraction before the first test slice.

    Returns:
        ``{"fold_maes": [...], "mean_mae": float|None, "n_folds_run": int}``
    """
    from ml.train import train
    from ml.evaluate import evaluate

    df = df.sort_values("time").reset_index(drop=True)
    n = len(df)
    if n < 100:
        return {
            "fold_maes": [],
            "mean_mae": None,
            "n_folds_run": 0,
            "error": "too_few_rows",
        }

    w = max(int(n * test_fraction), 1)
    train_end = max(int(n * min_train_fraction), w + 1)

    fold_maes: list[float] = []
    for _ in range(n_folds):
        if train_end + w > n:
            break
        train_raw = df.iloc[:train_end].copy()
        test_raw = df.iloc[train_end : train_end + w].copy()

        model, metrics = train(train_raw, weather_df=weather_df)
        encoding_map = metrics.get("pool_uid_encoding")
        train_medians_raw = metrics.get("train_medians", {})
        train_medians = pd.Series(train_medians_raw) if train_medians_raw else None

        report = evaluate(
            model,
            train_raw,
            test_raw,
            encoding_map=encoding_map,
            weather_df=weather_df,
            train_medians=train_medians,
        )
        fold_maes.append(report.model_mae)
        train_end += w

    return {
        "fold_maes": fold_maes,
        "mean_mae": float(np.mean(fold_maes)) if fold_maes else None,
        "n_folds_run": len(fold_maes),
    }
