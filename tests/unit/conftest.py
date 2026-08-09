"""Unit-test fixtures — block accidental Open-Meteo traffic."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _block_real_open_meteo_http(monkeypatch):
    """Fail loud if a unit test reaches the real Open-Meteo endpoints.

    Weather unit tests mock ``aiohttp.ClientSession`` (or ``fetch_weather`` /
    ``_fetch_weather_for_df``). Anything that forgets a mock and opens a real
    session against ``*.open-meteo.com`` must not silently spam the free tier.
    """
    import aiohttp

    original_get = aiohttp.ClientSession.get

    # ClientSession.get is sync and returns an async context manager.
    def guarded_get(self, url, *args, **kwargs):
        url_str = str(url)
        if "open-meteo.com" in url_str:
            raise RuntimeError(
                "Unit test attempted a real Open-Meteo HTTP request: "
                f"{url_str}. Mock aiohttp.ClientSession or "
                "ml.weather.fetch_weather / ml.retrain._fetch_weather_for_df."
            )
        return original_get(self, url, *args, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "get", guarded_get)
