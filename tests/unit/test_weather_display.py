"""weather_condition / build_weather_temps display helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ml.opening_hours import WeatherHint

from api.weather_display import build_weather_temps, weather_condition

Z = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 8, 8, 14, 0, tzinfo=Z)


@pytest.mark.parametrize(
    ("code", "label", "emoji"),
    [
        (0, "Klar", "☀️"),
        (1, "Meist klar", "🌤️"),
        (2, "Teils bewölkt", "⛅"),
        (3, "Bedeckt", "☁️"),
        (45, "Nebel", "🌫️"),
        (48, "Nebel", "🌫️"),
        (51, "Nieselregen", "🌦️"),
        (57, "Nieselregen", "🌦️"),
        (61, "Regen", "🌧️"),
        (67, "Regen", "🌧️"),
        (71, "Schnee", "❄️"),
        (77, "Schnee", "❄️"),
        (80, "Regenschauer", "🌦️"),
        (82, "Regenschauer", "🌦️"),
        (85, "Schneeschauer", "❄️"),
        (86, "Schneeschauer", "❄️"),
        (95, "Gewitter", "⛈️"),
        (99, "Gewitter", "⛈️"),
    ],
)
def test_known_wmo_codes(code, label, emoji):
    assert weather_condition(code) == (label, emoji)


def test_unknown_code_returns_none():
    assert weather_condition(999) is None
    assert weather_condition(-1) is None


def test_none_code_returns_none():
    assert weather_condition(None) is None


class TestBuildWeatherTemps:
    def test_full_water_and_air(self):
        hint = WeatherHint(temperature_c=21.5, precipitation_mm=0.0, weathercode=0)
        result = build_weather_temps(
            water_temp_c=26.0,
            observed_at=NOW - timedelta(minutes=6),
            source_modified_at=NOW - timedelta(hours=9),
            weather_hint=hint,
            now=NOW,
        )
        assert result == {
            "water_temp_c": 26.0,
            "air_temp_c": 21.5,
            "condition_label": "Klar",
            "condition_emoji": "☀️",
        }

    def test_air_only_when_water_missing(self):
        hint = WeatherHint(temperature_c=21.0, precipitation_mm=0.0, weathercode=1)
        result = build_weather_temps(
            water_temp_c=None,
            observed_at=NOW,
            source_modified_at=NOW,
            weather_hint=hint,
            now=NOW,
        )
        assert result["water_temp_c"] is None
        assert result["air_temp_c"] == 21.0
        assert result["condition_label"] == "Meist klar"

    def test_water_only_when_weather_missing(self):
        result = build_weather_temps(
            water_temp_c=26.0,
            observed_at=NOW - timedelta(minutes=6),
            source_modified_at=NOW - timedelta(hours=9),
            weather_hint=None,
            now=NOW,
        )
        assert result == {
            "water_temp_c": 26.0,
            "air_temp_c": None,
            "condition_label": None,
            "condition_emoji": None,
        }

    def test_hidden_when_neither_available(self):
        assert (
            build_weather_temps(
                water_temp_c=None,
                observed_at=None,
                source_modified_at=None,
                weather_hint=None,
                now=NOW,
            )
            is None
        )

    def test_stale_water_suppressed_keeps_air(self):
        hint = WeatherHint(temperature_c=18.0, precipitation_mm=0.0, weathercode=3)
        result = build_weather_temps(
            water_temp_c=26.0,
            observed_at=NOW - timedelta(hours=2),
            source_modified_at=NOW - timedelta(hours=9),
            weather_hint=hint,
            now=NOW,
        )
        assert result["water_temp_c"] is None
        assert result["air_temp_c"] == 18.0
