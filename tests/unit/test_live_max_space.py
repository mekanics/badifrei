"""CrowdMonitor max_space is the capacity source of truth for SEO/UI."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from api.main import _coerce_live_max_space, _latest_max_space


class TestCoerceLiveMaxSpace:
    def test_positive_int(self):
        assert _coerce_live_max_space(420) == 420

    def test_string_digits(self):
        assert _coerce_live_max_space("54") == 54

    def test_zero_is_none(self):
        assert _coerce_live_max_space(0) is None

    def test_negative_is_none(self):
        assert _coerce_live_max_space(-1) is None

    def test_none(self):
        assert _coerce_live_max_space(None) is None

    def test_invalid(self):
        assert _coerce_live_max_space("nope") is None


@pytest.mark.asyncio
class TestLatestMaxSpace:
    async def test_none_db_pool(self):
        assert await _latest_max_space(None, "BADI-1") is None

    async def test_returns_latest_positive(self):
        db = MagicMock()
        db.fetchrow = AsyncMock(return_value={"max_space": 422})
        assert await _latest_max_space(db, "BADI-1") == 422
        db.fetchrow.assert_awaited_once()
        sql = db.fetchrow.await_args.args[0]
        assert "max_space > 0" in sql
        assert db.fetchrow.await_args.args[1] == "BADI-1"

    async def test_no_row(self):
        db = MagicMock()
        db.fetchrow = AsyncMock(return_value=None)
        assert await _latest_max_space(db, "SSD-1") is None

    async def test_db_error_returns_none(self):
        db = MagicMock()
        db.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
        assert await _latest_max_space(db, "BADI-1") is None
