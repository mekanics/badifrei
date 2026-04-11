"""Inference-time policy for ``lag_1h`` when the DB has no fresh reading.

Training uses time-based ``lag_1h`` with NaNs filled from training medians.
At inference, a **2-hour freshness** window on the DB can leave ``lag_1h``
missing for many future hours; a naive recursion toward a single rolling
mean flattens the curve. This module blends **week-ago same-time** signal
with the rolling mean for cold start, then lightly blends with the
previous prediction for continuity — closer to training semantics without
runaway feedback.
"""

from __future__ import annotations


def anchor_from_history(
    lag_1w: float | None,
    rolling_mean_7d: float | None,
) -> float:
    """Scalar anchor when no fresh ``lag_1h`` is available.

    Prefer ``lag_1w`` (same DOW/hour last week), mix with ``rolling_mean_7d``
    when both exist — preserves day-of-week level better than rolling mean
    alone.
    """
    w = float(lag_1w) if lag_1w is not None else None
    r = float(rolling_mean_7d) if rolling_mean_7d is not None else None
    if w is not None and r is not None:
        return 0.55 * w + 0.45 * r
    if w is not None:
        return w
    if r is not None:
        return r
    return 0.0


def resolve_lag_1h_for_inference(
    lag_1h_db: float | None,
    lag_1w_db: float | None,
    rolling_mean_7d: float | None,
    last_pred: float | None,
) -> float:
    """Return the ``lag_1h`` value to feed into the regressor for this hour.

    Args:
        lag_1h_db: Reading within the freshness window before target time, or None.
        lag_1w_db: Week-ago occupancy (±30 min), or None.
        rolling_mean_7d: 7-day trailing mean before forecast window start, or None.
        last_pred: Previous hour's model output (after clip), or None on first step.
    """
    if lag_1h_db is not None:
        return float(lag_1h_db)

    anchor = anchor_from_history(lag_1w_db, rolling_mean_7d)
    if last_pred is None:
        return anchor
    # Light coupling to previous step — anchor carries DOW shape
    return 0.42 * float(last_pred) + 0.58 * anchor
