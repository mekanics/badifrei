"""Unit tests for TASK-025: lightweight DB migration runner.

All DB calls are mocked — no live database required.
Tests follow TDD spec from TASK-025.
"""
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(applied_filenames: list[str] | None = None) -> AsyncMock:
    """Build a mock asyncpg connection.

    - conn.fetch() returns rows simulating `schema_migrations` contents.
    - conn.execute() is a no-op by default.
    - conn.transaction() returns a proper async-context-manager mock.
    - conn.close() is an async no-op.
    """
    conn = AsyncMock()

    # fetch() returns schema_migrations rows
    rows = [{"filename": f} for f in (applied_filenames or [])]
    conn.fetch = AsyncMock(return_value=rows)

    # transaction() is synchronous in asyncpg, returns async ctx mgr
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)  # never suppress exceptions
    conn.transaction = MagicMock(return_value=txn)

    return conn


def _make_sql_file(directory: Path, name: str, content: str = "SELECT 1;") -> Path:
    path = directory / name
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# 1. schema_migrations table created
# ---------------------------------------------------------------------------

class TestSchemaMigrationsTableCreated:
    """Running the migrator on an empty DB creates the schema_migrations table."""

    async def test_schema_migrations_table_created(self, tmp_path):
        from scripts.migrate import run_migrations

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()

        conn = _make_conn()

        with patch("scripts.migrate.asyncpg.connect", AsyncMock(return_value=conn)):
            result = await run_migrations("postgresql://x/y", migrations_dir)

        assert result == 0

        # First execute call must contain CREATE TABLE IF NOT EXISTS schema_migrations
        all_execute_calls = conn.execute.call_args_list
        create_call_sql = all_execute_calls[0][0][0]
        assert "CREATE TABLE IF NOT EXISTS" in create_call_sql.upper()
        assert "schema_migrations" in create_call_sql.lower()

        # Columns: filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ
        assert "filename" in create_call_sql.lower()
        assert "applied_at" in create_call_sql.lower()


# ---------------------------------------------------------------------------
# 2. Migrations applied in lexicographic order
# ---------------------------------------------------------------------------

class TestMigrationsAppliedInOrder:
    """Three migration files must be executed in lexicographic filename order."""

    async def test_migrations_applied_in_order(self, tmp_path):
        from scripts.migrate import run_migrations

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()

        _make_sql_file(migrations_dir, "001_alpha.sql", "SELECT 'alpha';")
        _make_sql_file(migrations_dir, "003_gamma.sql", "SELECT 'gamma';")
        _make_sql_file(migrations_dir, "002_beta.sql",  "SELECT 'beta';")

        conn = _make_conn()  # nothing applied yet

        with patch("scripts.migrate.asyncpg.connect", AsyncMock(return_value=conn)):
            result = await run_migrations("postgresql://x/y", migrations_dir)

        assert result == 0

        # Collect the SQL strings passed to execute (skip first = CREATE TABLE)
        migration_sqls = [
            call_[0][0]
            for call_ in conn.execute.call_args_list
            if "schema_migrations" not in call_[0][0].lower()
               and "INSERT" not in call_[0][0].upper()
        ]

        assert migration_sqls == [
            "SELECT 'alpha';",
            "SELECT 'beta';",
            "SELECT 'gamma';",
        ]

        # All three filenames recorded in schema_migrations
        insert_calls = [
            call_[0][1]  # second positional arg = filename
            for call_ in conn.execute.call_args_list
            if len(call_[0]) > 1
        ]
        assert "001_alpha.sql" in insert_calls
        assert "002_beta.sql" in insert_calls
        assert "003_gamma.sql" in insert_calls


# ---------------------------------------------------------------------------
# 3. Idempotent on re-run
# ---------------------------------------------------------------------------

class TestIdempotentOnRerun:
    """Running the migrator twice must apply each migration exactly once."""

    async def test_idempotent_on_rerun(self, tmp_path):
        from scripts.migrate import run_migrations

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        _make_sql_file(migrations_dir, "001_init.sql", "SELECT 1;")
        _make_sql_file(migrations_dir, "002_update.sql", "SELECT 2;")

        # First run: nothing applied
        conn_first = _make_conn()
        with patch("scripts.migrate.asyncpg.connect", AsyncMock(return_value=conn_first)):
            result = await run_migrations("postgresql://x/y", migrations_dir)
        assert result == 0

        # Second run: both already applied
        conn_second = _make_conn(applied_filenames=["001_init.sql", "002_update.sql"])
        with patch("scripts.migrate.asyncpg.connect", AsyncMock(return_value=conn_second)):
            result = await run_migrations("postgresql://x/y", migrations_dir)
        assert result == 0

        # On the second run, only CREATE TABLE should be executed (no migration SQL)
        execute_calls_second = conn_second.execute.call_args_list
        # Only the CREATE TABLE call should appear (no INSERT, no migration SQL)
        non_create_calls = [
            c for c in execute_calls_second
            if "schema_migrations" not in c[0][0].lower()
               or "INSERT" in c[0][0].upper()
        ]
        assert len(non_create_calls) == 0


# ---------------------------------------------------------------------------
# 4. Skips already-applied migration
# ---------------------------------------------------------------------------

class TestSkipsAlreadyApplied:
    """Seed schema_migrations with 001_; runner must only apply 002_."""

    async def test_skips_already_applied(self, tmp_path):
        from scripts.migrate import run_migrations

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        _make_sql_file(migrations_dir, "001_init.sql",    "SELECT 'first';")
        _make_sql_file(migrations_dir, "002_weather.sql", "SELECT 'second';")

        # 001 already applied
        conn = _make_conn(applied_filenames=["001_init.sql"])

        with patch("scripts.migrate.asyncpg.connect", AsyncMock(return_value=conn)):
            result = await run_migrations("postgresql://x/y", migrations_dir)

        assert result == 0

        # 001 SQL must never be passed to execute
        all_sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert "SELECT 'first';" not in all_sqls

        # 002 SQL must be executed
        assert "SELECT 'second';" in all_sqls

        # Only 002 filename recorded (001 was already in the table)
        insert_filenames = [
            c[0][1]
            for c in conn.execute.call_args_list
            if len(c[0]) > 1
        ]
        assert "002_weather.sql" in insert_filenames
        assert "001_init.sql" not in insert_filenames


# ---------------------------------------------------------------------------
# 5. Failed migration halts runner, rolls back, not recorded
# ---------------------------------------------------------------------------

class TestFailedMigrationHaltsRunner:
    """Bad SQL in 002_ → runner exits non-zero; 003_ never runs; 002_ not recorded."""

    async def test_failed_migration_halts_runner(self, tmp_path):
        from scripts.migrate import run_migrations

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        _make_sql_file(migrations_dir, "001_good.sql",  "SELECT 'ok';")
        _make_sql_file(migrations_dir, "002_bad.sql",   "THIS IS NOT VALID SQL !!!;")
        _make_sql_file(migrations_dir, "003_after.sql", "SELECT 'after';")

        conn = _make_conn()

        # Make execute raise when it encounters the bad SQL
        def execute_side_effect(sql, *args, **kwargs):
            if "THIS IS NOT VALID SQL" in sql:
                raise Exception("syntax error at or near 'THIS'")

        conn.execute.side_effect = execute_side_effect

        with patch("scripts.migrate.asyncpg.connect", AsyncMock(return_value=conn)):
            result = await run_migrations("postgresql://x/y", migrations_dir)

        # Runner must return non-zero
        assert result != 0

        # 003_ must never be executed
        all_sqls = [c[0][0] for c in conn.execute.call_args_list]
        assert "SELECT 'after';" not in all_sqls

        # 002_ filename must NOT be recorded in schema_migrations
        insert_filenames = [
            c[0][1]
            for c in conn.execute.call_args_list
            if len(c[0]) > 1
        ]
        assert "002_bad.sql" not in insert_filenames

        # Transaction __aexit__ must have been called (rollback path)
        # conn.transaction() was called for 001 (succeeds) and 002 (fails)
        assert conn.transaction.call_count >= 1


# ---------------------------------------------------------------------------
# 6. Empty migrations directory is safe
# ---------------------------------------------------------------------------

class TestEmptyMigrationsDirIsSafe:
    """Pointing the migrator at an empty directory must exit zero."""

    async def test_empty_migrations_dir_is_safe(self, tmp_path):
        from scripts.migrate import run_migrations

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        # No .sql files

        conn = _make_conn()

        with patch("scripts.migrate.asyncpg.connect", AsyncMock(return_value=conn)):
            result = await run_migrations("postgresql://x/y", migrations_dir)

        assert result == 0

        # Only the CREATE TABLE call should have been made
        assert conn.execute.call_count == 1
        conn.fetch.assert_called_once()
        conn.close.assert_called_once()
