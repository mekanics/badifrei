"""HTTP tests for Markdown twins and discoverability wiring."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    from api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestIndexMarkdown:
    async def test_index_md_200(self, client):
        response = await client.get("/index.md")
        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]
        assert "charset=utf-8" in response.headers["content-type"]
        assert response.headers["cache-control"] == "public, max-age=3600"
        assert response.headers["x-robots-tag"] == "noindex"
        assert "badifrei.ch" in response.text
        assert "/bad/" in response.text and ".md" in response.text
        assert "llms.txt" in response.text


class TestPoolMarkdown:
    async def test_known_pool_md_200(self, client):
        response = await client.get("/bad/fb006.md")
        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]
        assert response.headers["cache-control"] == "public, max-age=180"
        assert response.headers["x-robots-tag"] == "noindex"
        assert "https://badifrei.ch/bad/fb006" in response.text
        assert "Aktuelle Auslastung" in response.text
        assert "Prognose heute" in response.text
        assert "## Profil" in response.text
        assert "## Öffnungszeiten" in response.text
        # Metadata description for Allenmoos is present in pool_metadata.json
        assert "Allenmoos" in response.text
        assert "Unterstrass" in response.text or "Freibad" in response.text
        assert "auf dieser Seite" not in response.text
        assert "auf der HTML-Seite (https://badifrei.ch/bad/fb006)" in response.text

    async def test_unknown_pool_md_404(self, client):
        response = await client.get("/bad/DOES-NOT-EXIST.md")
        assert response.status_code == 404

    async def test_html_pool_still_200(self, client):
        response = await client.get("/bad/fb006")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert 'rel="alternate"' in response.text
        assert 'type="text/markdown"' in response.text
        assert "/bad/fb006.md" in response.text


class TestDiscoverability:
    async def test_homepage_has_alternate_and_footer_llms(self, client):
        response = await client.get("/")
        assert response.status_code == 200
        assert 'rel="alternate"' in response.text
        assert (
            'href="/index.md"' in response.text
            or 'href="https://badifrei.ch/index.md"' in response.text
        )
        assert "/llms.txt" in response.text

    async def test_homepage_footer_credits_data_origins(self, client):
        response = await client.get("/")
        assert response.status_code == 200
        body = response.text
        assert "CrowdMonitor" in body
        assert "Baditicker" in body
        assert "Wassertemperatur" in body
        assert "Open-Meteo" in body
        assert 'href="https://open-meteo.com/"' in body
        assert 'data-umami-event-link-target="open-meteo"' in body

    async def test_llms_txt_points_at_md(self, client):
        from api.catalog import get_pools

        response = await client.get("/llms.txt")
        assert response.status_code == 200
        body = response.text
        pools = get_pools()
        assert "https://badifrei.ch/index.md" in body
        assert f"{len(pools)} pools" in body
        assert "max 4,500" not in body  # no hand-maintained capacities
        assert "Cache-Control" in response.headers
        assert response.headers["x-robots-tag"] == "noindex"

    async def test_sitemap_has_no_md_urls(self, client):
        response = await client.get("/sitemap.xml")
        assert response.status_code == 200
        assert ".md" not in response.text
