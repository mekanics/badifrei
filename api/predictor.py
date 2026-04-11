"""Model loading and inference."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import numpy as np
import pandas as pd
from ml.features import build_features, load_pool_metadata, FEATURE_COLUMNS
from ml.lag_policy import resolve_lag_1h_for_inference

if TYPE_CHECKING:
    import datetime as _dt

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent.parent / "ml" / "models"


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    """Normalize any timezone-aware datetime to UTC-naive for dict key lookups."""
    if dt is None:
        return None
    if hasattr(dt, "utcoffset") and dt.utcoffset() is not None:
        dt = dt.replace(tzinfo=None) - dt.utcoffset()  # convert to UTC naive
    return dt


class Predictor:
    def __init__(self):
        self.model = None
        self.model_version = "not-loaded"
        self._metadata = None
        self._encoding_map: dict[str, int] | None = None
        self._model_mtime: float | None = None
        self._reload_lock = asyncio.Lock()
        self._model_feature_names: list[str] | None = (
            None  # derived from model at load time
        )

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
                logger.warning(
                    f"No encoding sidecar at {encoding_path}; predictions may be wrong"
                )
                self._encoding_map = None

            # Eagerly load metadata so _get_metadata() never does I/O at inference time
            self._metadata = load_pool_metadata()

            # Derive feature list from the model itself so inference always matches training.
            # This prevents shape-mismatch errors when FEATURE_COLUMNS evolves (e.g. weather
            # columns added) but the on-disk model was trained before that change.
            try:
                booster_feature_names = self.model.get_booster().feature_names
                if booster_feature_names:
                    self._model_feature_names = list(booster_feature_names)
                    logger.info(
                        f"Model feature names ({len(self._model_feature_names)}): {self._model_feature_names}"
                    )
                else:
                    self._model_feature_names = None
            except Exception:
                self._model_feature_names = None

            logger.info(f"Model loaded: {model_path.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            return False

    def _reload_if_stale_sync(self) -> None:
        """Reload model if model_latest.ubj has been updated on disk (sync version for predict())."""
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

    async def _reload_if_stale(self) -> None:
        """Reload model if model_latest.ubj has been updated. Skips if reload already in progress."""
        if self._reload_lock.locked():
            return
        async with self._reload_lock:
            model_path = MODELS_DIR / "model_latest.ubj"
            if not model_path.exists():
                return
            try:
                stat_result = await asyncio.to_thread(model_path.stat)
                mtime = stat_result.st_mtime
                if mtime != self._model_mtime:
                    logger.info("Model file changed — reloading")
                    await asyncio.to_thread(self.load, model_path)
            except Exception as e:
                logger.warning(f"Could not check model mtime: {e}")

    def is_loaded(self) -> bool:
        return self.model is not None

    def _get_feature_columns(self) -> list[str]:
        """Return the feature columns the loaded model expects.

        Prefers the feature names stored in the model itself (set at training time).
        Falls back to the module-level FEATURE_COLUMNS constant only when the model
        does not expose feature names (e.g. very old XGBoost versions).
        """
        if self._model_feature_names:
            return self._model_feature_names
        return list(FEATURE_COLUMNS)

    def _get_metadata(self) -> dict:
        if self._metadata is None:
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
                        WHERE pool_uid = %s
                          AND time < %s
                          AND time >= %s - INTERVAL '2 hours'
                        ORDER BY time DESC
                        LIMIT 1
                        """,
                        (pool_uid, dt, dt),
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
                          AND time BETWEEN %s - INTERVAL '30 minutes'
                                       AND %s + INTERVAL '30 minutes'
                        ORDER BY ABS(EXTRACT(EPOCH FROM (time - %s)))
                        LIMIT 1
                        """,
                        (pool_uid, target, target, target),
                    )
                    row = cur.fetchone()
                    return float(row[0]) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Could not fetch week-ago occupancy: {e}")
            return None

    def _fetch_lag_sync(
        self, pool_uid: str, dt: datetime
    ) -> tuple[float | None, float | None]:
        """Fetch both lag features (recent + week-ago) in a single psycopg2 connection."""
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            return None, None
        try:
            import psycopg2

            target_week = dt - timedelta(days=7)
            conn = psycopg2.connect(database_url)
            try:
                with conn.cursor() as cur:
                    # lag_1h: most recent before dt, within 2h window
                    cur.execute(
                        """
                        SELECT occupancy_pct FROM pool_occupancy
                        WHERE pool_uid = %s
                          AND time < %s
                          AND time >= %s - INTERVAL '2 hours'
                        ORDER BY time DESC LIMIT 1
                        """,
                        (pool_uid, dt, dt),
                    )
                    row = cur.fetchone()
                    lag_1h = float(row[0]) if row else None

                    # lag_1w: closest to same time 7 days ago, ±30 min window
                    cur.execute(
                        """
                        SELECT occupancy_pct FROM pool_occupancy
                        WHERE pool_uid = %s
                          AND time BETWEEN %s - INTERVAL '30 minutes'
                                       AND %s + INTERVAL '30 minutes'
                        ORDER BY ABS(EXTRACT(EPOCH FROM (time - %s)))
                        LIMIT 1
                        """,
                        (pool_uid, target_week, target_week, target_week),
                    )
                    row = cur.fetchone()
                    lag_1w = float(row[0]) if row else None

                    return lag_1h, lag_1w
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Could not fetch lag features: {e}")
            return None, None

    async def _fetch_lag_features_batch(
        self,
        db_pool: "asyncpg.Pool | None",
        pool_uid: str,
        hours: list[datetime],
    ) -> tuple[list[float | None], list[float | None]]:
        """Fetch lag_1h and lag_1w for all hours in 2 async LATERAL queries.

        Returns (lag_1h_list, lag_1w_list) — None where no data available.
        Falls back to (all-None, all-None) if db_pool is None or query fails.
        """
        if db_pool is None:
            return [None] * len(hours), [None] * len(hours)

        try:
            recent_sql = """
                SELECT h.target_time, o.occupancy_pct
                FROM unnest($1::timestamptz[]) AS h(target_time)
                LEFT JOIN LATERAL (
                    SELECT occupancy_pct
                    FROM pool_occupancy
                    WHERE pool_uid = $2
                      AND time < h.target_time
                      AND time >= h.target_time - INTERVAL '2 hours'
                    ORDER BY time DESC
                    LIMIT 1
                ) o ON true
                """

            # Week-ago lag: ±30-min window around exact 7-day offset.
            # Training uses pd.Series.shift(freq="7D") which is also an exact 7D shift.
            # At inference, data is collected ~every 5–10 min, so any reading within ±30 min
            # is a faithful approximation of the training feature. Window also enables index use.
            week_sql = """
                SELECT h.target_time, o.occupancy_pct
                FROM unnest($1::timestamptz[]) AS h(target_time)
                LEFT JOIN LATERAL (
                    SELECT occupancy_pct
                    FROM pool_occupancy
                    WHERE pool_uid = $2
                      AND time BETWEEN h.target_time - INTERVAL '30 minutes'
                                   AND h.target_time + INTERVAL '30 minutes'
                    ORDER BY ABS(EXTRACT(EPOCH FROM (time - h.target_time)))
                    LIMIT 1
                ) o ON true
                """

            week_ago_times = [dt - timedelta(days=7) for dt in hours]
            recent_rows, week_rows = await asyncio.gather(
                db_pool.fetch(recent_sql, hours, pool_uid),
                db_pool.fetch(week_sql, week_ago_times, pool_uid),
            )

            # Normalize asyncpg datetimes to UTC-naive for reliable key lookup
            recent_map = {
                _to_utc_naive(r["target_time"]): r["occupancy_pct"] for r in recent_rows
            }
            week_map = {
                _to_utc_naive(r["target_time"]): r["occupancy_pct"] for r in week_rows
            }

            hours_naive = [_to_utc_naive(h) for h in hours]
            week_ago_naive = [_to_utc_naive(wa) for wa in week_ago_times]

            lag_1h = [
                float(recent_map[h]) if recent_map.get(h) is not None else None
                for h in hours_naive
            ]
            lag_1w = [
                float(week_map.get(wa)) if week_map.get(wa) is not None else None
                for wa in week_ago_naive
            ]
            return lag_1h, lag_1w

        except (
            asyncpg.PostgresError,
            asyncpg.InterfaceError,
            OSError,
            asyncio.TimeoutError,
        ) as e:
            logger.warning(f"Batch lag fetch failed: {e}", exc_info=True)
            return [None] * len(hours), [None] * len(hours)

    async def _fetch_rolling_mean_7d(
        self,
        db_pool: "asyncpg.Pool | None",
        pool_uid: str,
        before_dt: datetime | None,
    ) -> float | None:
        """Fetch 7-day rolling mean occupancy from DB using a time window.

        Uses ``time >= before_dt - 7 days`` to match the time-based rolling
        window used during training (``rolling("7D")``).
        """
        if db_pool is None or before_dt is None:
            return None
        try:
            row = await db_pool.fetchrow(
                """
                SELECT AVG(occupancy_pct) AS rolling_mean
                FROM pool_occupancy
                WHERE pool_uid = $1
                  AND time >= $2 - INTERVAL '7 days'
                  AND time < $2
                """,
                pool_uid,
                before_dt,
            )
            if row and row["rolling_mean"] is not None:
                return float(row["rolling_mean"])
            return None
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as e:
            logger.warning(f"Could not fetch rolling mean for {pool_uid}: {e}")
            return None

    async def _fetch_weather_multi_date_safe(
        self,
        dates: "list[datetime.date]",
        city: str,
    ) -> "pd.DataFrame | None":
        """Fetch weather for all unique *dates* for the given *city*.

        Uses fetch_weather_batch for efficient multi-date retrieval (DB cache → HTTP).
        Adds a ``city`` column so build_features can use the city-aware join path.
        Returns None on any failure or if *dates* is empty.
        """
        if not dates:
            return None
        try:
            from ml.weather import fetch_weather_batch

            df = await fetch_weather_batch(dates, city=city)
            if df.empty:
                return None
            df = df.copy()
            df["city"] = city
            return df
        except Exception as e:
            logger.warning(
                f"Weather batch fetch failed for city={city} dates={dates}: {e}"
            )
            return None

    async def predict_range_batch(
        self,
        pool_uid: str,
        hours: list[datetime],
        db_pool: "asyncpg.Pool | None" = None,
    ) -> list[float]:
        """Predict occupancy for all hours in one batched operation.

        Returns list of floats clipped to [0, 100].
        Returns [0.0] * len(hours) if model is not loaded.
        """
        if not self.is_loaded():
            return [0.0] * len(hours)

        # Check for model staleness once (not once per hour)
        await self._reload_if_stale()

        # Collect all unique dates across the requested hours and the pool's city
        unique_dates = sorted({h.date() for h in hours}) if hours else []
        pool_meta = self._get_metadata().get(pool_uid, {})
        city_slug = pool_meta.get("city", "zurich")

        # Fetch lag features, rolling mean, and weather (all dates) concurrently
        (lag_1h_list, lag_1w_list), rolling_mean_7d, weather_df = await asyncio.gather(
            self._fetch_lag_features_batch(db_pool, pool_uid, hours),
            self._fetch_rolling_mean_7d(db_pool, pool_uid, hours[0] if hours else None),
            self._fetch_weather_multi_date_safe(unique_dates, city_slug),
        )

        # Build 24-row DataFrame for vectorised feature engineering
        df = pd.DataFrame(
            [{"time": dt, "pool_uid": pool_uid, "occupancy_pct": 0.0} for dt in hours]
        )
        df_feat = build_features(
            df,
            metadata=self._get_metadata(),
            encoding_map=self._encoding_map,
            weather_df=weather_df,  # None → sensible defaults; real df → actual weather
        )

        # Inject real rolling mean (replaces dummy 0.0 computed from placeholder df)
        if rolling_mean_7d is not None:
            df_feat["rolling_mean_7d"] = rolling_mean_7d

        if len(df_feat) != len(hours):
            logger.error(
                f"build_features changed row count: expected {len(hours)}, got {len(df_feat)}. "
                "Lag features will be misaligned."
            )
        df_feat = df_feat.reset_index(drop=True)

        preds: list[float] = []
        last_pred: float | None = None

        for i in range(len(hours)):
            row = df_feat.iloc[[i]].copy()

            real_lag_1h = lag_1h_list[i]
            real_lag_1w = lag_1w_list[i]
            lag_in = resolve_lag_1h_for_inference(
                real_lag_1h,
                real_lag_1w,
                rolling_mean_7d,
                last_pred,
            )
            row["lag_1h"] = float(lag_in)
            if real_lag_1h is not None:
                last_pred = float(real_lag_1h)

            # lag_1w: real DB value or 0 (week-ago data; not recursive)
            row["lag_1w"] = float(real_lag_1w) if real_lag_1w is not None else 0.0

            # Hard-zero for closed hours
            if int(row["is_open"].iloc[0]) == 0:
                preds.append(0.0)
                last_pred = 0.0
                continue

            feat_cols = self._get_feature_columns()
            X = row.reindex(columns=feat_cols).fillna(0)
            pred = float(np.clip(self.model.predict(X)[0], 0.0, 100.0))
            preds.append(pred)
            last_pred = pred

        return preds

    def predict(self, pool_uid: str, dt: datetime) -> float:
        """Predict occupancy % for a pool at a given datetime."""
        if not self.is_loaded():
            raise RuntimeError("Model not loaded")

        # Reload model if it has been updated (e.g. by retrain service)
        self._reload_if_stale_sync()

        # Fetch real lag values from DB (gracefully falls back to None → 0)
        lag_1h, lag_1w = self._fetch_lag_sync(pool_uid, dt)

        # Build a single-row DataFrame for inference
        df = pd.DataFrame(
            [
                {
                    "time": dt,
                    "pool_uid": pool_uid,
                    "occupancy_pct": 0.0,  # placeholder target
                }
            ]
        )
        df_feat = build_features(
            df,
            metadata=self._get_metadata(),
            encoding_map=self._encoding_map,
            lag_1h_override=lag_1h,
            lag_1w_override=lag_1w,
        )

        # Short-circuit: if pool is closed, return 0 immediately
        if int(df_feat["is_open"].iloc[0]) == 0:
            return 0.0

        # Fill NaNs (lag features will be NaN for single-row inference if DB unavailable)
        feat_cols = self._get_feature_columns()
        X = df_feat.reindex(columns=feat_cols).fillna(0)

        pred = float(self.model.predict(X)[0])
        return float(np.clip(pred, 0.0, 100.0))


# Singleton — loaded at startup
predictor = Predictor()
