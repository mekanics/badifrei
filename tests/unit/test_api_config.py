"""Tests for api.config Settings."""

import api.config as config
import pytest


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    # Always clear the live module function — reload would orphan a cached instance.
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


class TestSettings:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        monkeypatch.delenv("UMAMI_SCRIPT_URL", raising=False)
        monkeypatch.delenv("UMAMI_WEBSITE_ID", raising=False)
        monkeypatch.delenv("WEEKLY_INSIGHTS_CACHE_TTL_SECONDS", raising=False)

        # Ignore local .env so defaults are asserted in isolation
        settings = config.Settings(_env_file=None)
        assert settings.database_url is None
        assert settings.cors_origins == ["https://badifrei.ch"]
        assert settings.umami_script_url == ""
        assert settings.umami_website_id == ""
        assert settings.weekly_insights_cache_ttl_seconds == 3600
        assert settings.umami_csp_origin == ""

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS", "https://a.example, https://b.example"
        )
        monkeypatch.setenv("UMAMI_SCRIPT_URL", "https://analytics.example/script.js")
        monkeypatch.setenv("UMAMI_WEBSITE_ID", "abc")
        monkeypatch.setenv("WEEKLY_INSIGHTS_CACHE_TTL_SECONDS", "120")

        config.get_settings.cache_clear()
        settings = config.get_settings()
        assert settings.database_url == "postgresql://u:p@localhost/db"
        assert settings.cors_origins == ["https://a.example", "https://b.example"]
        assert settings.umami_csp_origin == "https://analytics.example"
        assert settings.umami_website_id == "abc"
        assert settings.weekly_insights_cache_ttl_seconds == 120
