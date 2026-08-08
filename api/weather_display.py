"""WMO weathercode → German label + emoji for the detail-page temperature display.

Mirrors the ``api.city_display`` pattern: a small lookup table with a single
pure function. Unknown codes return ``None`` so callers can omit the
label/emoji rather than inventing "Unbekannt".
"""

from __future__ import annotations

from datetime import datetime

from api.water_temperature import fresh_water_temp

# (label, emoji) keyed by WMO code groups. Ranges are expanded at import time.
_CONDITION_BY_CODE: dict[int, tuple[str, str]] = {}


def _register(codes: range | tuple[int, ...], label: str, emoji: str) -> None:
    for code in codes:
        _CONDITION_BY_CODE[code] = (label, emoji)


_register((0,), "Klar", "☀️")
_register((1,), "Meist klar", "🌤️")
_register((2,), "Teils bewölkt", "⛅")
_register((3,), "Bedeckt", "☁️")
_register((45, 48), "Nebel", "🌫️")
_register(range(51, 58), "Nieselregen", "🌦️")
_register(range(61, 68), "Regen", "🌧️")
_register(range(71, 78), "Schnee", "❄️")
_register(range(80, 83), "Regenschauer", "🌦️")
_register((85, 86), "Schneeschauer", "❄️")
_register(range(95, 100), "Gewitter", "⛈️")


def weather_condition(code: int | None) -> tuple[str, str] | None:
    """Return ``(German label, emoji)`` for a WMO weathercode, or None."""
    if code is None:
        return None
    try:
        return _CONDITION_BY_CODE.get(int(code))
    except (TypeError, ValueError):
        return None


def build_weather_temps(
    *,
    water_temp_c: float | None,
    observed_at: datetime | None,
    source_modified_at: datetime | None,
    weather_hint,
    now: datetime,
) -> dict | None:
    """Build template/API display payload, or None when nothing is available.

    Keys: ``water_temp_c``, ``air_temp_c``, ``condition_label``, ``condition_emoji``.
    Water temp is gated by ``fresh_water_temp``; air temp/condition come from
    the city WeatherHint. Returns None when both sides are absent.
    """
    water = fresh_water_temp(
        water_temp_c,
        observed_at=observed_at,
        source_modified_at=source_modified_at,
        now=now,
    )
    air = None
    code = None
    if weather_hint is not None:
        air = weather_hint.temperature_c
        code = weather_hint.weathercode
    condition = weather_condition(code)
    label = condition[0] if condition else None
    emoji = condition[1] if condition else None

    if water is None and air is None:
        return None
    return {
        "water_temp_c": water,
        "air_temp_c": air,
        "condition_label": label,
        "condition_emoji": emoji,
    }
