"""Unit tests for database writer module."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from collector.ws_client import PoolReading


def make_reading(uid="SSD-5", name="Kaeferberg", fill=27, maxspace=70, freespace=43):
    return PoolReading(uid=uid, name=name, currentfill=fill, maxspace=maxspace, freespace=freespace)


class TestWriteBatch:
    async def test_write_empty_list_returns_zero(self):
        from collector.db import write_batch
        result = await write_batch([])
        assert result == 0

    async def test_skip_zero_maxspace(self):
        """Pools with max_space=0 (sensor offline) should be skipped."""
        from collector.db import write_batch
        readings = [make_reading(uid="SSD-1", maxspace=0, freespace=0)]
        with patch("collector.db.get_pool") as mock_get_pool:
            result = await write_batch(readings)
        assert result == 0
        mock_get_pool.assert_not_called()

    def _make_pool_mock(self, mock_conn):
        """
        asyncpg pool.acquire() is NOT a coroutine — it returns an async context manager.
        So acquire must be a plain MagicMock, not AsyncMock.
        """
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=ctx)
        return mock_pool

    async def test_write_single_record(self):
        from collector.db import write_batch
        reading = make_reading()
        ts = datetime(2026, 2, 27, 14, 0, 0, tzinfo=timezone.utc)

        mock_conn = AsyncMock()
        mock_pool = self._make_pool_mock(mock_conn)

        with patch("collector.db.get_pool", AsyncMock(return_value=mock_pool)):
            result = await write_batch([reading], timestamp=ts)

        assert result == 1
        mock_conn.executemany.assert_called_once()
        call_args = mock_conn.executemany.call_args
        records = call_args[0][1]
        assert len(records) == 1
        assert records[0][1] == "SSD-5"  # pool_uid
        assert records[0][3] == 27       # current_fill
        assert records[0][4] == 70       # max_space

    async def test_write_batch_of_22(self):
        from collector.db import write_batch
        readings = [make_reading(uid=f"POOL-{i}", fill=i, maxspace=100, freespace=100 - i) for i in range(22)]

        mock_conn = AsyncMock()
        mock_pool = self._make_pool_mock(mock_conn)

        with patch("collector.db.get_pool", AsyncMock(return_value=mock_pool)):
            result = await write_batch(readings)

        assert result == 22
        records = mock_conn.executemany.call_args[0][1]
        assert len(records) == 22

    async def test_mixed_valid_invalid(self):
        """Mix of valid and zero-maxspace pools — only valid ones written."""
        from collector.db import write_batch
        readings = [
            make_reading(uid="VALID-1", maxspace=100),
            make_reading(uid="OFFLINE", maxspace=0, freespace=0),
            make_reading(uid="VALID-2", maxspace=200),
        ]
        mock_conn = AsyncMock()
        mock_pool = self._make_pool_mock(mock_conn)

        with patch("collector.db.get_pool", AsyncMock(return_value=mock_pool)):
            result = await write_batch(readings)

        assert result == 2
        records = mock_conn.executemany.call_args[0][1]
        uids = [r[1] for r in records]
        assert "OFFLINE" not in uids

    async def test_timestamp_utc_when_not_provided(self):
        """If no timestamp given, uses UTC now."""
        from collector.db import write_batch
        reading = make_reading()
        mock_conn = AsyncMock()
        mock_pool = self._make_pool_mock(mock_conn)

        with patch("collector.db.get_pool", AsyncMock(return_value=mock_pool)):
            result = await write_batch([reading])

        records = mock_conn.executemany.call_args[0][1]
        ts = records[0][0]
        assert ts.tzinfo is not None  # must be timezone-aware
