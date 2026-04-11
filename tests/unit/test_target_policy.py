"""Tests for ml.target_policy."""

import pandas as pd


def test_clip_series():
    from ml.target_policy import clip_occupancy_target

    s = pd.Series([-5.0, 50.0, 150.0])
    out = clip_occupancy_target(s)
    assert list(out) == [0.0, 50.0, 100.0]


def test_clip_float():
    from ml.target_policy import clip_occupancy_target

    assert clip_occupancy_target(150.0) == 100.0
    assert clip_occupancy_target(-1.0) == 0.0
