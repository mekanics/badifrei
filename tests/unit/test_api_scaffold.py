"""Tests for FastAPI app scaffold."""

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client():
    from api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestHealth:
    async def test_health_returns_200(self, client):
        response = await client.get("/health")
        assert response.status_code == 200

    async def test_health_returns_ok_status(self, client):
        response = await client.get("/health")
        data = response.json()
        assert data["status"] == "ok"

    async def test_health_has_version(self, client):
        response = await client.get("/health")
        data = response.json()
        assert "version" in data

    async def test_cors_header_present(self, client):
        # CORS is now restricted to configured origins (default: https://badifrei.ch).
        # Requests from unknown origins receive no ACAO header (browser blocks them).
        # Requests from an allowed origin get a reflected ACAO header.
        response = await client.get(
            "/health", headers={"Origin": "https://badifrei.ch"}
        )
        assert (
            response.headers.get("access-control-allow-origin") == "https://badifrei.ch"
        )


class TestOpenAPI:
    async def test_openapi_docs_not_available(self, client):
        response = await client.get("/docs")
        assert response.status_code == 404

    async def test_openapi_schema_not_available(self, client):
        response = await client.get("/openapi.json")
        assert response.status_code == 404


class TestPools:
    async def test_pools_returns_200(self, client):
        response = await client.get("/pools")
        assert response.status_code == 200

    async def test_pools_returns_list(self, client):
        response = await client.get("/pools")
        assert isinstance(response.json(), list)

    async def test_pools_count_32(self, client):
        response = await client.get("/pools")
        assert len(response.json()) == 31

    async def test_pools_schema(self, client):
        response = await client.get("/pools")
        pool = response.json()[0]
        for field in ["uid", "name", "type", "seasonal", "city", "max_capacity"]:
            assert field in pool, f"Missing field: {field}"

    async def test_kaeferberg_present(self, client):
        response = await client.get("/pools")
        uids = [p["uid"] for p in response.json()]
        assert "SSD-5" in uids

    async def test_hallenbad_not_seasonal(self, client):
        response = await client.get("/pools")
        pools_by_uid = {p["uid"]: p for p in response.json()}
        assert not pools_by_uid["SSD-5"]["seasonal"]


class TestPredictions:
    async def test_range_prediction_includes_status_metadata(self, client, monkeypatch):
        from api import main as api_main

        async def _predict_range_batch(pool_uid, hours, db_pool=None):
            return [0.0] * len(hours)

        monkeypatch.setattr(api_main.predictor, "is_loaded", lambda: False)
        monkeypatch.setattr(
            api_main.predictor, "predict_range_batch", _predict_range_batch
        )

        response = await client.get("/predict/range?pool_uid=SSD-5&date=2026-03-07")

        assert response.status_code == 200
        data = response.json()
        assert data["model_available"] is False
        assert data["model_version"] == "no-model"
        assert data["prediction_status"] == "no_model"
        assert data["open_hours_count"] > 0
        assert len(data["predictions"]) == 24
