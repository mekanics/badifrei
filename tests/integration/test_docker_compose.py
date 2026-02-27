"""
Integration tests for docker compose stack.
These tests require a running docker compose stack.
Run with: make test-integration

Marked with @pytest.mark.integration — skipped by default.
"""
import os
import time
import pytest
import httpx
import asyncpg


pytestmark = pytest.mark.integration

API_URL = os.getenv("API_URL", "http://localhost:8000")
DB_URL = os.getenv("TEST_DATABASE_URL", "postgresql://badi:badi@localhost:5432/badi")


@pytest.fixture(scope="module")
def api_client():
    with httpx.Client(base_url=API_URL, timeout=10) as client:
        yield client


class TestAPIHealth:
    def test_api_health_via_compose(self, api_client):
        """API /health returns 200 after compose up."""
        response = api_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_pools_endpoint(self, api_client):
        response = api_client.get("/pools")
        assert response.status_code == 200
        assert len(response.json()) > 0

    def test_predict_endpoint_no_model(self, api_client):
        """Predict works even without a trained model (returns placeholder)."""
        response = api_client.get("/predict?pool_uid=SSD-5&dt_str=2026-03-07T15:00:00")
        assert response.status_code == 200

    def test_range_endpoint_no_model(self, api_client):
        response = api_client.get("/predict/range?pool_uid=SSD-5&date=2026-03-07")
        assert response.status_code == 200
        assert len(response.json()["predictions"]) == 24


class TestDataCollection:
    def test_db_accessible(self):
        """Can connect to TimescaleDB."""
        import asyncio
        async def check():
            conn = await asyncpg.connect(DB_URL)
            result = await conn.fetchval("SELECT 1")
            await conn.close()
            return result
        result = asyncio.run(check())
        assert result == 1

    def test_hypertable_exists(self):
        """pool_occupancy hypertable was created by init.sql."""
        import asyncio
        async def check():
            conn = await asyncpg.connect(DB_URL)
            result = await conn.fetchval(
                "SELECT COUNT(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'pool_occupancy'"
            )
            await conn.close()
            return result
        result = asyncio.run(check())
        assert result == 1
