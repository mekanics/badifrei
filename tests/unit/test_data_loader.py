"""Unit tests for data loader module."""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import pytest

from ml.data_loader import InsufficientDataError


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 2, 1, tzinfo=timezone.utc)


def make_rows(n=1000, pool_uid="SSD-5"):
    """Create n fake asyncpg rows."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        t = base + timedelta(hours=i)
        rows.append({
            "time": t,
            "pool_uid": pool_uid,
            "pool_name": "Kaeferberg",
            "current_fill": i % 70,
            "max_space": 70,
            "free_space": 70 - (i % 70),
            "occupancy_pct": (i % 70) / 70 * 100,
        })
    return rows


def patch_db(rows):
    """Helper: patch asyncpg.connect to return mock with given rows."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)
    mock_conn.close = AsyncMock()
    return patch("ml.data_loader.asyncpg.connect", AsyncMock(return_value=mock_conn)), mock_conn


class TestLoadData:
    async def test_returns_dataframe(self):
        from ml.data_loader import load_data
        rows = make_rows(1000)
        ctx, _ = patch_db(rows)
        with ctx:
            df = await load_data(START, END, min_records=100)
        assert isinstance(df, pd.DataFrame)

    async def test_returns_correct_columns(self):
        from ml.data_loader import load_data
        rows = make_rows(1000)
        ctx, _ = patch_db(rows)
        with ctx:
            df = await load_data(START, END, min_records=100)
        for col in ["time", "pool_uid", "pool_name", "current_fill", "max_space", "free_space", "occupancy_pct"]:
            assert col in df.columns

    async def test_raises_insufficient_data_error(self):
        from ml.data_loader import load_data
        rows = make_rows(5)  # Only 5 rows
        ctx, _ = patch_db(rows)
        with ctx:
            with pytest.raises(InsufficientDataError):
                await load_data(START, END, min_records=1000)

    async def test_time_column_is_utc(self):
        from ml.data_loader import load_data
        rows = make_rows(1000)
        ctx, _ = patch_db(rows)
        with ctx:
            df = await load_data(START, END, min_records=100)
        assert df["time"].dt.tz is not None

    async def test_occupancy_pct_is_float(self):
        from ml.data_loader import load_data
        rows = make_rows(1000)
        ctx, _ = patch_db(rows)
        with ctx:
            df = await load_data(START, END, min_records=100)
        assert df["occupancy_pct"].dtype == float

    async def test_date_range_passed_to_query(self):
        from ml.data_loader import load_data
        rows = make_rows(1000)
        ctx, mock_conn = patch_db(rows)
        with ctx:
            await load_data(START, END, min_records=100)
        call_args = mock_conn.fetch.call_args[0]
        assert START in call_args
        assert END in call_args

    async def test_connection_closed_on_success(self):
        from ml.data_loader import load_data
        rows = make_rows(1000)
        ctx, mock_conn = patch_db(rows)
        with ctx:
            await load_data(START, END, min_records=100)
        mock_conn.close.assert_called_once()

    async def test_connection_closed_on_error(self):
        """Even if InsufficientDataError is raised, connection must be closed."""
        from ml.data_loader import load_data
        rows = make_rows(5)
        ctx, mock_conn = patch_db(rows)
        with ctx:
            with pytest.raises(InsufficientDataError):
                await load_data(START, END, min_records=1000)
        mock_conn.close.assert_called_once()
