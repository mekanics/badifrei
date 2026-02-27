"""Unit tests for automated retraining module."""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np
import pandas as pd


def make_df(n=1500):
    """Minimal DataFrame with required columns."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = []
    for i in range(n):
        records.append({
            "time": base + timedelta(hours=i),
            "pool_uid": f"SSD-{(i % 3) + 1}",
            "pool_name": "Pool",
            "current_fill": 30 + (i % 40),
            "max_space": 100,
            "free_space": 70 - (i % 40),
            "occupancy_pct": float(30 + (i % 40)),
        })
    return pd.DataFrame(records)


class TestRetrainJob:
    async def test_retrain_job_runs_with_sufficient_data(self):
        from ml.retrain import retrain_job
        df = make_df(1500)
        mock_model = MagicMock()
        mock_metrics = {"mae": 5.0, "rmse": 7.0}
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True

        with patch("ml.retrain.load_data", AsyncMock(return_value=df)), \
             patch("ml.retrain.train", return_value=(mock_model, mock_metrics)), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("ml.retrain.evaluate", return_value=mock_report), \
             patch("ml.retrain._prune_old_models"):
            await retrain_job()

    async def test_retrain_job_skips_on_insufficient_data(self):
        from ml.retrain import retrain_job
        from ml.data_loader import InsufficientDataError

        with patch("ml.retrain.load_data", AsyncMock(side_effect=InsufficientDataError("Not enough"))), \
             patch("ml.retrain.train") as mock_train:
            await retrain_job()
            mock_train.assert_not_called()

    async def test_retrain_job_saves_model(self):
        from ml.retrain import retrain_job
        df = make_df(1500)
        mock_model = MagicMock()
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True

        with patch("ml.retrain.load_data", AsyncMock(return_value=df)), \
             patch("ml.retrain.train", return_value=(mock_model, {"mae": 5.0})), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")) as mock_save, \
             patch("ml.retrain.evaluate", return_value=mock_report), \
             patch("ml.retrain._prune_old_models"):
            await retrain_job()
            mock_save.assert_called_once()

    async def test_retrain_job_calls_evaluate(self):
        from ml.retrain import retrain_job
        df = make_df(1500)
        mock_model = MagicMock()
        mock_report = MagicMock()
        mock_report.model_mae = 5.0
        mock_report.baseline_mae = 8.0
        mock_report.beats_baseline = True

        with patch("ml.retrain.load_data", AsyncMock(return_value=df)), \
             patch("ml.retrain.train", return_value=(mock_model, {"mae": 5.0})), \
             patch("ml.retrain.save_model", return_value=Path("/tmp/model.ubj")), \
             patch("ml.retrain.evaluate", return_value=mock_report) as mock_eval, \
             patch("ml.retrain._prune_old_models"):
            await retrain_job()
            mock_eval.assert_called_once()


class TestPruneOldModels:
    def test_prune_removes_old_files(self, tmp_path):
        from ml.retrain import _prune_old_models
        import ml.retrain as mr
        import time

        # Create an "old" model file
        old_file = tmp_path / "model_2025-01-01.ubj"
        old_file.write_bytes(b"fake model")
        # Set mtime to 40 days ago
        old_mtime = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
        import os
        os.utime(old_file, (old_mtime, old_mtime))

        # Create a "recent" model file
        new_file = tmp_path / "model_2026-02-27.ubj"
        new_file.write_bytes(b"fake model")

        with patch.object(mr, "MODELS_DIR", tmp_path):
            _prune_old_models(keep_days=30)

        assert not old_file.exists()
        assert new_file.exists()

    def test_prune_skips_symlinks(self, tmp_path):
        from ml.retrain import _prune_old_models
        import ml.retrain as mr
        import os

        # Create a real file and symlink to it
        real_file = tmp_path / "model_2025-01-01.ubj"
        real_file.write_bytes(b"fake model")
        old_mtime = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
        os.utime(real_file, (old_mtime, old_mtime))

        symlink = tmp_path / "model_latest.ubj"
        symlink.symlink_to(real_file.name)

        with patch.object(mr, "MODELS_DIR", tmp_path):
            _prune_old_models(keep_days=30)

        # Real file pruned, symlink left (symlinks are skipped)
        assert not real_file.exists()
        assert symlink.is_symlink()

    def test_prune_keeps_recent_files(self, tmp_path):
        from ml.retrain import _prune_old_models
        import ml.retrain as mr

        recent_file = tmp_path / "model_2026-02-27.ubj"
        recent_file.write_bytes(b"fake model")

        with patch.object(mr, "MODELS_DIR", tmp_path):
            _prune_old_models(keep_days=30)

        assert recent_file.exists()
