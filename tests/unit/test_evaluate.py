"""Tests for model evaluation module."""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone, timedelta


def make_df(n_pools=3, hours_per_pool=300):
    records = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for pool_idx in range(n_pools):
        uid = f"SSD-{pool_idx + 1}"
        for h in range(hours_per_pool):
            t = base + timedelta(hours=h)
            fill = int(30 + 20 * np.sin(h * 2 * np.pi / 24))
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


@pytest.fixture(scope="module")
def trained_model_and_data():
    from ml.train import train, time_based_split

    df = make_df(n_pools=3, hours_per_pool=300)
    model, metrics = train(df, test_fraction=0.2)
    train_df, test_df = time_based_split(df, test_fraction=0.2)
    return model, train_df, test_df, metrics


class TestNaiveBaseline:
    def test_baseline_same_length_as_test(self, trained_model_and_data):
        from ml.evaluate import naive_baseline_predict

        model, train_df, test_df, _ = trained_model_and_data
        preds = naive_baseline_predict(train_df, test_df)
        assert len(preds) == len(test_df)

    def test_baseline_in_range(self, trained_model_and_data):
        from ml.evaluate import naive_baseline_predict

        model, train_df, test_df, _ = trained_model_and_data
        preds = naive_baseline_predict(train_df, test_df)
        assert (preds >= 0).all() and (preds <= 100).all()

    def test_baseline_varies(self, trained_model_and_data):
        from ml.evaluate import naive_baseline_predict

        model, train_df, test_df, _ = trained_model_and_data
        preds = naive_baseline_predict(train_df, test_df)
        assert preds.std() > 0


class TestEvaluate:
    def test_evaluate_returns_report(self, trained_model_and_data):
        from ml.evaluate import evaluate, EvaluationReport

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert isinstance(report, EvaluationReport)

    def test_evaluate_mae_positive(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert report.model_mae >= 0
        assert report.baseline_mae >= 0

    def test_evaluate_rmse_gte_mae(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert report.model_rmse >= report.model_mae
        assert report.baseline_rmse >= report.baseline_mae

    def test_evaluate_per_pool_count(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert len(report.per_pool) == test_df["pool_uid"].nunique()

    def test_worst_pool_is_valid_uid(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert report.worst_pool in test_df["pool_uid"].unique().tolist()

    def test_best_pool_is_valid_uid(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert report.best_pool in test_df["pool_uid"].unique().tolist()

    def test_n_test_correct(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert report.n_test == len(test_df)

    def test_beats_baseline_is_bool(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert isinstance(report.beats_baseline, bool)

    def test_per_pool_mae_all_positive(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        for p in report.per_pool:
            assert p.mae >= 0

    def test_worst_pool_has_highest_mae(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        worst_mae = next(
            p.mae for p in report.per_pool if p.pool_uid == report.worst_pool
        )
        for p in report.per_pool:
            assert p.mae <= worst_mae + 1e-9

    def test_stratified_mae_keys_present(self, trained_model_and_data):
        from ml.evaluate import evaluate

        model, train_df, test_df, _ = trained_model_and_data
        report = evaluate(model, train_df, test_df)
        assert isinstance(report.stratified, dict)
        assert "peak_hours_mae" in report.stratified
        assert "weekday_mae" in report.stratified
