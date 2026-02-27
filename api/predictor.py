"""Model loading and inference."""
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent.parent / "ml" / "models"


class Predictor:
    def __init__(self):
        self.model = None
        self.model_version = "not-loaded"
        self._metadata = None

    def load(self, path: Path | None = None) -> bool:
        """Load model from disk. Returns True if successful."""
        try:
            from ml.train import load_model
            model_path = path or (MODELS_DIR / "model_latest.ubj")
            if not model_path.exists():
                logger.warning(f"No model found at {model_path}")
                return False
            self.model = load_model(model_path)
            self.model_version = model_path.stem.replace("model_", "")
            logger.info(f"Model loaded: {model_path.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return False

    def is_loaded(self) -> bool:
        return self.model is not None

    def _get_metadata(self) -> dict:
        if self._metadata is None:
            from ml.features import load_pool_metadata
            self._metadata = load_pool_metadata()
        return self._metadata

    def predict(self, pool_uid: str, dt: datetime) -> float:
        """Predict occupancy % for a pool at a given datetime."""
        if not self.is_loaded():
            raise RuntimeError("Model not loaded")

        from ml.features import build_features, FEATURE_COLUMNS

        # Build a single-row DataFrame for inference
        df = pd.DataFrame([{
            "time": dt,
            "pool_uid": pool_uid,
            "occupancy_pct": 0.0,  # placeholder target
        }])
        df_feat = build_features(df, metadata=self._get_metadata())

        # Fill NaNs (lag features will be NaN for single-row inference)
        X = df_feat[FEATURE_COLUMNS].fillna(0)

        pred = float(self.model.predict(X)[0])
        return float(np.clip(pred, 0.0, 100.0))


# Singleton — loaded at startup
predictor = Predictor()
