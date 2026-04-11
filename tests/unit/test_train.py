"""Unit tests for model training module."""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import numpy as np
import pandas as pd
import pytest


def make_training_df(n_pools=3, hours_per_pool=200):
    """Create synthetic training data."""
    records = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    pool_uids = [f"SSD-{i}" for i in range(1, n_pools + 1)]

    for uid in pool_uids:
        for h in range(hours_per_pool):
            t = base + timedelta(hours=h)
            fill = int(30 + 20 * np.sin(h * 2 * np.pi / 24))  # Daily cycle
            records.append(
                {
                    "time": t,
                    "pool_uid": uid,
                    "pool_name": f"Pool {uid}",
                    "current_fill": fill,
                    "max_space": 100,
                    "free_space": 100 - fill,
                    "occupancy_pct": float(fill),
                }
            )
    return pd.DataFrame(records)


class TestTimeSplit:
    def test_split_maintains_order(self):
        from ml.train import time_based_split

        df = make_training_df(n_pools=1, hours_per_pool=100)
        train, test = time_based_split(df, test_fraction=0.2)
        assert train["time"].max() <= test["time"].min()

    def test_split_ratio(self):
        from ml.train import time_based_split

        df = make_training_df(n_pools=1, hours_per_pool=100)
        train, test = time_based_split(df, test_fraction=0.2)
        assert len(train) == 80
        assert len(test) == 20

    def test_no_overlap(self):
        from ml.train import time_based_split

        df = make_training_df(n_pools=1, hours_per_pool=100)
        train, test = time_based_split(df, test_fraction=0.2)
        assert len(train) + len(test) == len(df)

    def test_default_fraction(self):
        from ml.train import time_based_split

        df = make_training_df(n_pools=1, hours_per_pool=100)
        train, test = time_based_split(df)
        assert len(train) == 80
        assert len(test) == 20

    def test_custom_fraction(self):
        from ml.train import time_based_split

        df = make_training_df(n_pools=1, hours_per_pool=100)
        train, test = time_based_split(df, test_fraction=0.1)
        assert len(train) == 90
        assert len(test) == 10


class TestPrepareXY:
    def test_returns_correct_feature_count(self):
        from ml.train import prepare_xy
        from ml.features import build_features, FEATURE_COLUMNS

        df = make_training_df(n_pools=2, hours_per_pool=50)
        df_feat = build_features(df)
        X, y = prepare_xy(df_feat)
        assert X.shape[1] == len(FEATURE_COLUMNS)

    def test_no_nan_in_X(self):
        from ml.train import prepare_xy
        from ml.features import build_features

        df = make_training_df(n_pools=2, hours_per_pool=50)
        df_feat = build_features(df)
        X, y = prepare_xy(df_feat)
        assert not X.isna().any().any()

    def test_target_in_range(self):
        from ml.train import prepare_xy
        from ml.features import build_features

        df = make_training_df(n_pools=2, hours_per_pool=50)
        df_feat = build_features(df)
        _, y = prepare_xy(df_feat)
        assert y.between(0, 100).all()

    def test_target_clips_over_100(self):
        from ml.train import prepare_xy
        from ml.features import build_features

        df = make_training_df(n_pools=1, hours_per_pool=20)
        df_feat = build_features(df)
        df_feat.loc[df_feat.index[0], "occupancy_pct"] = 150.0
        _, y = prepare_xy(df_feat)
        assert float(y.iloc[0]) == 100.0

    def test_returns_dataframe_and_series(self):
        from ml.train import prepare_xy
        from ml.features import build_features

        df = make_training_df(n_pools=2, hours_per_pool=50)
        df_feat = build_features(df)
        X, y = prepare_xy(df_feat)
        assert isinstance(X, pd.DataFrame)
        assert isinstance(y, pd.Series)

    def test_x_and_y_same_length(self):
        from ml.train import prepare_xy
        from ml.features import build_features

        df = make_training_df(n_pools=2, hours_per_pool=50)
        df_feat = build_features(df)
        X, y = prepare_xy(df_feat)
        assert len(X) == len(y)


class TestPrepareXYMedians:
    def test_explicit_medians_used_for_fillna(self):
        from ml.train import prepare_xy
        from ml.features import build_features, FEATURE_COLUMNS

        df = make_training_df(n_pools=2, hours_per_pool=50)
        df_feat = build_features(df)
        # Compute train medians, then verify they're used on a separate df
        X_train, _ = prepare_xy(df_feat)
        train_medians = X_train.median()
        X_with_medians, _ = prepare_xy(df_feat, medians=train_medians)
        assert not X_with_medians.isna().any().any()

    def test_medians_none_falls_back_to_self_median(self):
        from ml.train import prepare_xy
        from ml.features import build_features

        df = make_training_df(n_pools=2, hours_per_pool=50)
        df_feat = build_features(df)
        X, _ = prepare_xy(df_feat, medians=None)
        assert not X.isna().any().any()


class TestTrain:
    def test_train_returns_model_and_metrics(self):
        from ml.train import train

        df = make_training_df(n_pools=3, hours_per_pool=200)
        model, metrics = train(df, test_fraction=0.2)
        assert model is not None
        assert "mae" in metrics
        assert "rmse" in metrics

    def test_predictions_in_range(self):
        from ml.train import train, prepare_xy
        from ml.features import build_features

        df = make_training_df(n_pools=3, hours_per_pool=200)
        model, _ = train(df, test_fraction=0.2)
        df_feat = build_features(df)
        X, _ = prepare_xy(df_feat)
        preds = model.predict(X)
        preds = np.clip(preds, 0, 100)
        assert (preds >= 0).all() and (preds <= 100).all()

    def test_metrics_has_per_pool(self):
        from ml.train import train

        df = make_training_df(n_pools=3, hours_per_pool=200)
        _, metrics = train(df)
        assert "per_pool" in metrics
        assert len(metrics["per_pool"]) > 0

    def test_metrics_has_feature_importance(self):
        from ml.train import train
        from ml.features import FEATURE_COLUMNS

        df = make_training_df(n_pools=3, hours_per_pool=200)
        _, metrics = train(df)
        assert "feature_importance" in metrics
        assert len(metrics["feature_importance"]) == len(FEATURE_COLUMNS)

    def test_metrics_has_counts(self):
        from ml.train import train

        df = make_training_df(n_pools=3, hours_per_pool=200)
        _, metrics = train(df)
        assert "n_train" in metrics
        assert "n_test" in metrics
        assert metrics["n_train"] > 0
        assert metrics["n_test"] > 0

    def test_metrics_has_trained_at(self):
        from ml.train import train

        df = make_training_df(n_pools=3, hours_per_pool=200)
        _, metrics = train(df)
        assert "trained_at" in metrics
        # Should be parseable ISO datetime
        dt = datetime.fromisoformat(metrics["trained_at"])
        assert dt.tzinfo is not None

    def test_per_pool_has_expected_keys(self):
        from ml.train import train

        df = make_training_df(n_pools=3, hours_per_pool=200)
        _, metrics = train(df)
        for uid, pool_metrics in metrics["per_pool"].items():
            assert "mae" in pool_metrics
            assert "rmse" in pool_metrics
            assert "n" in pool_metrics

    def test_metrics_has_train_medians(self):
        from ml.train import train
        from ml.features import FEATURE_COLUMNS

        df = make_training_df(n_pools=3, hours_per_pool=200)
        _, metrics = train(df)
        assert "train_medians" in metrics
        assert isinstance(metrics["train_medians"], dict)
        for col in FEATURE_COLUMNS:
            assert col in metrics["train_medians"]

    def test_metrics_has_n_estimators_best(self):
        from ml.train import train

        df = make_training_df(n_pools=3, hours_per_pool=200)
        _, metrics = train(df)
        assert "n_estimators_best" in metrics
        assert isinstance(metrics["n_estimators_best"], int)
        assert metrics["n_estimators_best"] > 0


class TestSaveLoadModel:
    def test_save_creates_model_file(self, tmp_path):
        from ml.train import train, save_model
        import ml.train as mt

        df = make_training_df(n_pools=2, hours_per_pool=100)
        model, metrics = train(df)

        with patch.object(mt, "MODELS_DIR", tmp_path):
            path = save_model(model, metrics)

        assert path.exists()

    def test_save_creates_training_report(self, tmp_path):
        from ml.train import train, save_model
        import ml.train as mt

        df = make_training_df(n_pools=2, hours_per_pool=100)
        model, metrics = train(df)

        with patch.object(mt, "MODELS_DIR", tmp_path):
            save_model(model, metrics)

        report_path = tmp_path / "training_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert "mae" in report

    def test_save_load_roundtrip(self, tmp_path):
        from ml.train import train, save_model, load_model
        from ml.features import build_features
        from ml.train import prepare_xy
        import ml.train as mt

        df = make_training_df(n_pools=2, hours_per_pool=100)
        model, metrics = train(df)

        with patch.object(mt, "MODELS_DIR", tmp_path):
            model_path = save_model(model, metrics)
            loaded = load_model(model_path)

        # Loaded model should produce same predictions
        df_feat = build_features(df)
        X, _ = prepare_xy(df_feat)
        orig_preds = model.predict(X)
        loaded_preds = loaded.predict(X)
        np.testing.assert_array_almost_equal(orig_preds, loaded_preds)

    def test_save_creates_symlink(self, tmp_path):
        from ml.train import train, save_model
        import ml.train as mt

        df = make_training_df(n_pools=2, hours_per_pool=100)
        model, metrics = train(df)

        with patch.object(mt, "MODELS_DIR", tmp_path):
            save_model(model, metrics)

        latest = tmp_path / "model_latest.ubj"
        assert latest.is_symlink()

    def test_save_twice_updates_symlink(self, tmp_path):
        from ml.train import train, save_model
        import ml.train as mt

        df = make_training_df(n_pools=2, hours_per_pool=100)
        model, metrics = train(df)

        with patch.object(mt, "MODELS_DIR", tmp_path):
            save_model(model, metrics)
            # Second save should not raise even though symlink exists
            save_model(model, metrics)

        latest = tmp_path / "model_latest.ubj"
        assert latest.is_symlink()
