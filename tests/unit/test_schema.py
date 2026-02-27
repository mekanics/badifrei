"""Tests for DB schema definition - validates SQL structure."""
import re
from pathlib import Path

SQL_PATH = Path(__file__).parent.parent.parent / "docker" / "init.sql"

def test_init_sql_exists():
    assert SQL_PATH.exists(), f"init.sql not found at {SQL_PATH}"

def test_sql_creates_pool_occupancy_table():
    sql = SQL_PATH.read_text()
    assert "pool_occupancy" in sql

def test_sql_has_timescaledb_extension():
    sql = SQL_PATH.read_text()
    assert "timescaledb" in sql.lower()

def test_sql_creates_hypertable():
    sql = SQL_PATH.read_text()
    assert "create_hypertable" in sql.lower()

def test_sql_has_index():
    sql = SQL_PATH.read_text()
    assert "CREATE INDEX" in sql

def test_sql_has_required_columns():
    sql = SQL_PATH.read_text()
    for col in ["time", "pool_uid", "pool_name", "current_fill", "max_space", "free_space", "occupancy_pct"]:
        assert col in sql, f"Column '{col}' missing from schema"

def test_sql_occupancy_pct_generated():
    sql = SQL_PATH.read_text()
    assert "GENERATED ALWAYS AS" in sql
    assert "NULLIF" in sql  # protects against division by zero
