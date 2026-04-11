"""Training and evaluation target semantics for occupancy percentage.

CrowdMonitor can emit ``occupancy_pct`` above 100% when ``max_space`` or
metadata capacity is misaligned. The model and UI are defined on
**0–100%**; training targets are clipped to that range so the learner
matches inference (``np.clip(..., 0, 100)``).

**Forecast audience:** ``pool_metadata.opening_hours`` describe **public**
access windows. Rows outside those windows are treated as closed
(``is_open=0``); sensor activity during "closed" hours is still in the DB
but the **forecast** is forced to 0 when closed — i.e. public expectation,
not private/school-only use. To forecast sensor-level occupancy, metadata
would need separate schedules or a dedicated flag (not implemented here).
"""

from __future__ import annotations

import os

import pandas as pd

OCCUPANCY_TARGET_MIN = 0.0
OCCUPANCY_TARGET_MAX = 100.0


def clip_occupancy_target(
    series: "pd.Series | float",
    lo: float | None = None,
    hi: float | None = None,
) -> "pd.Series | float":
    """Clip occupancy percentage to ``[lo, hi]`` (defaults 0–100).

    ``TRAIN_TARGET_MAX`` env overrides the upper bound for experiments.
    """
    lo = float(lo if lo is not None else OCCUPANCY_TARGET_MIN)
    hi = float(hi if hi is not None else float(os.getenv("TRAIN_TARGET_MAX", "100")))
    hi = min(hi, OCCUPANCY_TARGET_MAX)
    if isinstance(series, pd.Series):
        return series.clip(lower=lo, upper=hi)
    return float(max(lo, min(hi, float(series))))
