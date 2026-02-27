"""Tests for FastAPI app scaffold."""
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client():
    from api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
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
        response = await client.get("/health", headers={"Origin": "http://example.com"})
        assert response.headers.get("access-control-allow-origin") == "*"


class TestOpenAPI:
    async def test_openapi_docs_available(self, client):
        response = await client.get("/docs")
        assert response.status_code == 200

    async def test_openapi_schema_available(self, client):
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Badi Predictor"


class TestPools:
    async def test_pools_returns_200(self, client):
        response = await client.get("/pools")
        assert response.status_code == 200

    async def test_pools_returns_list(self, client):
        response = await client.get("/pools")
        assert isinstance(response.json(), list)

    async def test_pools_count_22(self, client):
        response = await client.get("/pools")
        assert len(response.json()) == 22

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
        assert pools_by_uid["SSD-5"]["seasonal"] == False
