#!/usr/bin/env python3
"""Lightweight DB migration runner for badi-predictor.

Scans ``docker/migrations/`` (or a custom directory) for ``*.sql`` files,
executes each in lexicographic order inside a single transaction, and records
successfully applied migrations in a ``schema_migrations`` tracking table.
Already-applied migrations are skipped, making the runner fully idempotent.

Usage::

    python scripts/migrate.py [--migrations-dir docker/migrations/]

Environment:
    DATABASE_URL  asyncpg-compatible connection string (required).
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_CREATE_MIGRATIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT        PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

_SELECT_APPLIED = "SELECT filename FROM schema_migrations"
_INSERT_APPLIED = "INSERT INTO schema_migrations (filename) VALUES ($1)"


# ---------------------------------------------------------------------------
# Core runner (async)
# ---------------------------------------------------------------------------

async def run_migrations(db_url: str, migrations_dir: Path) -> int:
    """Apply all pending migrations and return an exit code (0 = success).

    Args:
        db_url:         asyncpg connection URL.
        migrations_dir: Path to directory containing ``*.sql`` migration files.

    Returns:
        0 on success, 1 on the first migration error (runner halts immediately).
    """
    conn = await asyncpg.connect(db_url)
    try:
        # Ensure the tracking table exists
        await conn.execute(_CREATE_MIGRATIONS_TABLE)

        # Determine which migrations have already been applied
        rows = await conn.fetch(_SELECT_APPLIED)
        applied: set[str] = {r["filename"] for r in rows}

        # Walk migration files in strict lexicographic order
        migration_files = sorted(migrations_dir.glob("*.sql"))

        for migration_file in migration_files:
            filename = migration_file.name

            if filename in applied:
                print(f"[skip]  {filename} (already applied)")
                continue

            sql = migration_file.read_text()
            print(f"[apply] {filename} ...", flush=True)

            try:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(_INSERT_APPLIED, filename)
                print(f"[done]  {filename}")
            except Exception as exc:  # noqa: BLE001
                print(f"[error] {filename}: {exc}", file=sys.stderr)
                return 1

    finally:
        await conn.close()

    return 0


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Badi Predictor — lightweight DB migration runner",
    )
    parser.add_argument(
        "--migrations-dir",
        default="docker/migrations",
        metavar="DIR",
        help="Directory containing *.sql migration files (default: docker/migrations)",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable is not set", file=sys.stderr)
        sys.exit(1)

    migrations_dir = Path(args.migrations_dir)
    if not migrations_dir.is_dir():
        print(
            f"ERROR: migrations directory not found: {migrations_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    exit_code = asyncio.run(run_migrations(db_url, migrations_dir))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
