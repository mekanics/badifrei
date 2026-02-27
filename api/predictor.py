"""Model loading and inference."""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
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
        self._encoding_map: dict[str, int] | None = None
        self._model_mtime: float | None = None

    def load(self, path: Path | None = None) -> bool:
        """Load model (and encoding sidecar) from disk. Returns True if successful."""
        try:
            from ml.train import load_model
            model_path = path or (MODELS_DIR / "model_latest.ubj")
            if not model_path.exists():
                logger.warning(f"No model found at {model_path}")
                return False

            self.model = load_model(model_path)
            self.model_version = model_path.stem.replace("model_", "")
            self._model_mtime = model_path.stat().st_mtime

            # Load encoding sidecar
            encoding_path = model_path.with_suffix(".json")
            if encoding_path.exists():
                with open(encoding_path) as f:
                    self._encoding_map = json.load(f)
                logger.info(f"Encoding map loaded: {len(self._encoding_map)} pools")
            else:
                logger.warning(f"No encoding sidecar at {encoding_path}; predictions may be wrong")
                self._encoding_map = None

            logger.info(f"Model loaded: {model_path.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return False

    def _reload_if_stale(self) -> None:
        """Reload model if model_latest.ubj has been updated on disk."""
        model_path = MODELS_DIR / "model_latest.ubj"
        if not model_path.exists():
            return
        try:
            mtime = model_path.stat().st_mtime
            if mtime != self._model_mtime:
                logger.info("Model file changed — reloading")
                self.load(model_path)
        except Exception as e:
            logger.warning(f"Could not check model mtime: {e}")

    def is_loaded(self) -> bool:
        return self.model is not None

    def _get_metadata(self) -> dict:
        if self._metadata is None:
            from ml.features import load_pool_metadata
            self._metadata = load_pool_metadata()
        return self._metadata

    def _fetch_recent_occupancy(self, pool_uid: str, dt: datetime) -> float | None:
        """Fetch the most recent occupancy reading before *dt* for *pool_uid*.

        Returns None if DB is unavailable or no data exists.
        This is a synchronous call using psycopg2 (single connection, rare).
        """
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            return None
        try:
            import psycopg2
            conn = psycopg2.connect(database_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT occupancy_pct
                        FROM pool_occupancy
                        WHERE pool_uid = %s AND time < %s
                        ORDER BY time DESC
                        LIMIT 1
                        """,
                        (pool_uid, dt),
                    )
                    row = cur.fetchone()
                    return float(row[0]) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Could not fetch recent occupancy: {e}")
            return None

    def _fetch_week_ago_occupancy(self, pool_uid: str, dt: datetime) -> float | None:
        """Fetch the occupancy reading closest to *dt* - 7 days for *pool_uid*.

        Returns None if DB is unavailable or no data exists.
        """
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            return None
        try:
            import psycopg2
            target = dt - timedelta(days=7)
            conn = psycopg2.connect(database_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT occupancy_pct
                        FROM pool_occupancy
                        WHERE pool_uid = %s
                        ORDER BY ABS(EXTRACT(EPOCH FROM (time - %s)))
                        LIMIT 1
                        """,
                        (pool_uid, target),
                    )
                    row = cur.fetchone()
                    return float(row[0]) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Could not fetch week-ago occupancy: {e}")
            return None

    def predict(self, pool_uid: str, dt: datetime) -> float:
        """Predict occupancy % for a pool at a given datetime."""
        if not self.is_loaded():
            raise RuntimeError("Model not loaded")

        # Reload model if it has been updated (e.g. by retrain service)
        self._reload_if_stale()

        from ml.features import build_features, FEATURE_COLUMNS

        # Fetch real lag values from DB (gracefully falls back to None → 0)
        lag_1h = self._fetch_recent_occupancy(pool_uid, dt)
        lag_1w = self._fetch_week_ago_occupancy(pool_uid, dt)

        # Build a single-row DataFrame for inference
        df = pd.DataFrame([{
            "time": dt,
            "pool_uid": pool_uid,
            "occupancy_pct": 0.0,  # placeholder target
        }])
        df_feat = build_features(
            df,
            metadata=self._get_metadata(),
            encoding_map=self._encoding_map,
            lag_1h_override=lag_1h,
            lag_1w_override=lag_1w,
        )

        # Fill NaNs (lag features will be NaN for single-row inference if DB unavailable)
        X = df_feat[FEATURE_COLUMNS].fillna(0)

        pred = float(self.model.predict(X)[0])
        return float(np.clip(pred, 0.0, 100.0))


# Singleton — loaded at startup
predictor = Predictor()
