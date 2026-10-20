"""WMO weather-code mapping: label, icon id and scene id (single source of truth)."""

from __future__ import annotations

from typing import Any

# code -> (label, icon, scene)
_DAY_TABLE: dict[int, tuple[str, str, str]] = {
    0: ("Clear sky", "sun", "clear"),
    1: ("Mainly clear", "sun-cloud", "clear"),
    2: ("Partly cloudy", "sun-cloud", "partly-cloudy"),
    3: ("Overcast", "cloud", "cloudy"),
    45: ("Fog", "fog", "fog"),
    48: ("Fog", "fog", "fog"),
    51: ("Drizzle", "drizzle", "rain"),
    53: ("Drizzle", "drizzle", "rain"),
    55: ("Drizzle", "drizzle", "rain"),
    56: ("Freezing drizzle", "freezing-rain", "freezing-rain"),
    57: ("Freezing drizzle", "freezing-rain", "freezing-rain"),
    61: ("Rain", "rain", "rain"),
    63: ("Rain", "rain", "rain"),
    65: ("Rain", "rain", "rain"),
    66: ("Freezing rain", "freezing-rain", "freezing-rain"),
    67: ("Freezing rain", "freezing-rain", "freezing-rain"),
    71: ("Snowfall", "snow", "snow"),
    73: ("Snowfall", "snow", "snow"),
    75: ("Snowfall", "snow", "snow"),
    77: ("Snow grains", "snow", "snow"),
    80: ("Rain showers", "rain", "rain"),
    81: ("Rain showers", "rain", "rain"),
    82: ("Rain showers", "rain", "rain"),
    85: ("Snow showers", "snow", "snow"),
    86: ("Snow showers", "snow", "snow"),
    95: ("Thunderstorm", "thunderstorm", "thunderstorm"),
    96: ("Thunderstorm with hail", "thunderstorm", "thunderstorm"),
    99: ("Thunderstorm with hail", "thunderstorm", "thunderstorm"),
}

# Icons that have a distinct night variant.
_NIGHT_ICON = {"sun": "moon", "sun-cloud": "moon-cloud"}

_UNKNOWN = ("Conditions unavailable", "unknown", "neutral")


def _coerce_code(code: Any) -> int | None:
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, float) and code.is_integer():
        return int(code)
    return None


def describe_weather_code(code: Any, is_day: bool | int | None = True) -> dict[str, Any]:
    """Return {code, label, icon, scene}. Unknown/missing codes get a neutral fallback."""
    numeric = _coerce_code(code)
    label, icon, scene = _DAY_TABLE.get(numeric, _UNKNOWN) if numeric is not None else _UNKNOWN
    if numeric in _DAY_TABLE and is_day is not None and not is_day:
        icon = _NIGHT_ICON.get(icon, icon)
    return {
        "code": numeric,
        "label": label,
        "icon": icon,
        "scene": scene,
    }