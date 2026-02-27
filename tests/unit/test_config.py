"""Unit tests for collector.config — written TDD-style."""


def test_config_has_ws_url():
    from collector.config import settings

    assert settings.ws_url.startswith("wss://")


def test_config_has_database_url():
    from collector.config import settings

    assert "postgresql" in settings.database_url


def test_config_ws_url_from_env(monkeypatch):
    monkeypatch.setenv("WS_URL", "wss://test.example.com/api")
    # reimport to pick up env change
    import importlib

    import collector.config as cfg

    importlib.reload(cfg)
    assert cfg.settings.ws_url == "wss://test.example.com/api"
