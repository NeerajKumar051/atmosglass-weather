"""Open-Meteo client: geocoding + forecast, with validation and normalization.

This is the only module that knows provider URLs and request parameters.
Everything it returns is plain, JSON-serializable Python data.
"""

from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import requests

from app.utils.weather_codes import describe_weather_code

logger = logging.getLogger(__name__)

# --- Fixed provider settings (move to app/config.py in a later phase) --------
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

CONNECT_TIMEOUT = 5.0  # seconds
READ_TIMEOUT = 10.0  # seconds
MAX_RETRIES = 2  # extra attempts after the first one (transient errors only)
BACKOFF_SECONDS = 0.5  # 0.5s, then 1.0s
RETRY_STATUS_CODES = frozenset({500, 502, 503, 504})
MAX_UNSUPPORTED_DROPS = 3  # max variables dropped after the API rejects them

MAX_QUERY_LENGTH = 100
MIN_SEARCH_LENGTH = 2  # the API returns nothing for 0-1 characters
DEFAULT_RESULT_COUNT = 6
MAX_RESULT_COUNT = 10
FORECAST_DAYS = 7

# --- Field maps: normalized name -> provider variable --------------------------
_CURRENT_NUMERIC = {
    "temperature": "temperature_2m", "apparent_temperature": "apparent_temperature",
    "humidity": "relative_humidity_2m", "precipitation": "precipitation", "rain": "rain",
    "showers": "showers", "snowfall": "snowfall", "cloud_cover": "cloud_cover",
    "pressure": "pressure_msl", "wind_speed": "wind_speed_10m",
    "wind_direction": "wind_direction_10m", "wind_gusts": "wind_gusts_10m",
    "visibility": "visibility", "uv_index": "uv_index",
}
_HOURLY_NUMERIC = {
    "temperature": "temperature_2m", "apparent_temperature": "apparent_temperature",
    "humidity": "relative_humidity_2m", "precipitation_probability": "precipitation_probability",
    "rain": "rain", "showers": "showers", "snowfall": "snowfall", "snow_depth": "snow_depth",
    "pressure": "pressure_msl", "surface_pressure": "surface_pressure",
    "cloud_cover_mid": "cloud_cover_mid", "cloud_cover_high": "cloud_cover_high",
    "visibility": "visibility", "wind_speed": "wind_speed_10m", "uv_index": "uv_index",
}
_DAILY_NUMERIC = {
    "temperature_max": "temperature_2m_max", "temperature_min": "temperature_2m_min",
    "daylight_duration": "daylight_duration", "sunshine_duration": "sunshine_duration",
    "uv_index_max": "uv_index_max",
    "precipitation_probability_max": "precipitation_probability_max",
    "precipitation_sum": "precipitation_sum", "rain_sum": "rain_sum",
    "wind_speed_max": "wind_speed_10m_max", "wind_gusts_max": "wind_gusts_10m_max",
}
_DAILY_TEXT = {
    "sunrise": "sunrise", "sunset": "sunset", "moonrise": "moonrise", "moonset": "moonset",
}


def _variables(*groups: Any) -> tuple[str, ...]:
    names: list[str] = []
    for group in groups:
        names.extend(group.values() if isinstance(group, dict) else group)
    return tuple(dict.fromkeys(names))


CURRENT_VARIABLES = _variables(_CURRENT_NUMERIC, ("is_day", "weather_code"))
HOURLY_VARIABLES = _variables(_HOURLY_NUMERIC, ("is_day", "weather_code"))
DAILY_VARIABLES = _variables(_DAILY_NUMERIC, _DAILY_TEXT, ("weather_code",))

# Variables the UI cannot work without: if the API rejects one of these, fail loudly.
# Everything else is optional and is dropped (and reported) if the API rejects it.
_CORE_VARIABLES = {
    "current": frozenset({"temperature_2m", "weather_code"}),
    "hourly": frozenset({"temperature_2m", "weather_code"}),
    "daily": frozenset({"weather_code", "temperature_2m_max", "temperature_2m_min"}),
}

# Documented defaults (we request no unit overrides); used if the response omits units.
_DEFAULT_UNITS = {
    "temperature": "°C", "humidity": "%", "precipitation": "mm", "pressure": "hPa",
    "wind_speed": "km/h", "wind_direction": "°", "visibility": "m", "uv_index": "",
    "snow_depth": "m", "duration": "s",
}


class WeatherClientError(Exception):
    """Expected failure: finite error code + friendly message (+ developer-only reason)."""

    def __init__(self, code: str, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason  # provider diagnostic; log it, never show it in the UI


# --- Validation ----------------------------------------------------------------
def validate_query(query: Any) -> str:
    """Trim/collapse whitespace; reject non-strings, control characters, long input."""
    if not isinstance(query, str):
        raise WeatherClientError("VALIDATION_ERROR", "Enter a city name.")
    cleaned = " ".join(query.split())
    if len(cleaned) > MAX_QUERY_LENGTH:
        raise WeatherClientError(
            "VALIDATION_ERROR", f"City names must be {MAX_QUERY_LENGTH} characters or fewer."
        )
    if any(unicodedata.category(ch).startswith("C") for ch in cleaned):
        raise WeatherClientError("VALIDATION_ERROR", "The city name contains invalid characters.")
    return cleaned


def _num(value: Any) -> float | None:
    """Finite number or None (bools and NaN/inf are rejected)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _flag(value: Any) -> bool | None:
    number = _num(value)
    return None if number is None else number != 0


def validate_coordinates(latitude: Any, longitude: Any) -> tuple[float, float]:
    lat, lon = _num(latitude), _num(longitude)
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise WeatherClientError("VALIDATION_ERROR", "The selected location is not valid.")
    return lat, lon


# --- Client --------------------------------------------------------------------
class OpenMeteoClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = "AtmosGlassWeather/0.1 (desktop app)"
        self._session = session
        self._sleep = sleep

    # Public API -------------------------------------------------------------
    def search_locations(
        self, query: Any, count: int = DEFAULT_RESULT_COUNT
    ) -> list[dict[str, Any]]:
        """Return normalized location suggestions ([] when nothing matches)."""
        cleaned = validate_query(query)
        if len(cleaned) < MIN_SEARCH_LENGTH:
            return []  # no request for empty / one-character input
        count = max(1, min(int(count), MAX_RESULT_COUNT))
        payload = self._get_json(
            GEOCODING_URL,
            {"name": cleaned, "count": count, "language": "en", "format": "json"},
        )
        results = payload.get("results")
        if results is None:
            return []  # Open-Meteo omits "results" when nothing matches
        if not isinstance(results, list):
            raise WeatherClientError("INVALID_RESPONSE", "Location search returned unexpected data.")
        locations = [loc for loc in (_normalize_location(item) for item in results) if loc]
        if results and not locations:
            raise WeatherClientError("INVALID_RESPONSE", "Location search returned unexpected data.")
        return locations

    def get_forecast(
        self, latitude: Any, longitude: Any, location_name: str | None = None
    ) -> dict[str, Any]:
        """Return one normalized payload: location, timezone, units, current, hourly, daily.

        If the API rejects an optional variable by name, that variable is dropped, the
        request is repeated (bounded), and the dropped names are listed in
        `skipped_variables`. Rejection of a core variable raises instead.
        """
        lat, lon = validate_coordinates(latitude, longitude)
        variables = {
            "current": list(CURRENT_VARIABLES),
            "hourly": list(HOURLY_VARIABLES),
            "daily": list(DAILY_VARIABLES),
        }
        skipped: list[str] = []
        while True:
            params = {
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                **{block: ",".join(names) for block, names in variables.items()},
                "timezone": "auto",
                "forecast_days": FORECAST_DAYS,
            }
            try:
                payload = self._get_json(FORECAST_URL, params)
                break
            except WeatherClientError as err:
                dropped = (
                    _drop_rejected_variable(variables, err.reason)
                    if err.code == "PROVIDER_ERROR"
                    else None
                )
                if dropped is None or len(skipped) >= MAX_UNSUPPORTED_DROPS:
                    raise
                skipped.append(dropped)
                logger.warning("Dropped unsupported forecast variable: %s", dropped)
        result = _normalize_forecast(payload, lat, lon, location_name)
        result["skipped_variables"] = skipped
        return result

    # Transport --------------------------------------------------------------
    def _get_json(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        error: WeatherClientError
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.get(
                    url, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
                )
            except requests.exceptions.Timeout:
                error = WeatherClientError("TIMEOUT", "The weather service took too long to respond.")
            except requests.exceptions.ConnectionError:
                error = WeatherClientError("NETWORK_ERROR", "Could not reach the weather service. Check your connection.")
            except requests.exceptions.RequestException:
                raise WeatherClientError("NETWORK_ERROR", "The weather request failed.") from None
            else:
                status = response.status_code
                if status == 429:
                    raise WeatherClientError("RATE_LIMITED", "The weather service is limiting requests. Try again in a minute.")
                if status in RETRY_STATUS_CODES:
                    error = WeatherClientError("PROVIDER_ERROR", "The weather service is temporarily unavailable.")
                elif not 200 <= status < 300:
                    reason = _error_reason(response)
                    logger.warning("Open-Meteo returned HTTP %s: %s", status, reason)
                    raise WeatherClientError("PROVIDER_ERROR", "The weather service rejected the request.", reason)
                else:
                    return self._parse_json(response)
            logger.warning("Open-Meteo request failed (%s), attempt %d", error.code, attempt + 1)
            if attempt < MAX_RETRIES:
                self._sleep(BACKOFF_SECONDS * (2**attempt))
        raise error

    @staticmethod
    def _parse_json(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            raise WeatherClientError("INVALID_RESPONSE", "The weather service returned unreadable data.") from None
        if not isinstance(payload, dict):
            raise WeatherClientError("INVALID_RESPONSE", "The weather service returned unexpected data.")
        if payload.get("error") is True:
            reason = _text(payload.get("reason"))
            raise WeatherClientError("PROVIDER_ERROR", "The weather service could not process the request.", reason)
        return payload


def _error_reason(response: Any) -> str | None:
    """Best-effort extraction of Open-Meteo's {"error": true, "reason": "..."} text."""
    try:
        body = response.json()
    except ValueError:
        return None
    reason = _text(body.get("reason")) if isinstance(body, dict) else None
    return reason[:300] if reason else None


def _drop_rejected_variable(variables: dict[str, list[str]], reason: str | None) -> str | None:
    """Remove one optional variable named in the API's error reason; return 'block.name'."""
    if not reason:
        return None
    match = re.search(r"for key (\w+)", reason)
    blocks = [match.group(1)] if match and match.group(1) in variables else list(variables)
    for block in blocks:
        for name in variables[block]:
            if name in _CORE_VARIABLES[block]:
                continue
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", reason):
                variables[block].remove(name)
                return f"{block}.{name}"
    return None


# --- Normalization ---------------------------------------------------------------
def _normalize_location(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    name = _text(item.get("name"))
    lat, lon = _num(item.get("latitude")), _num(item.get("longitude"))
    if name is None or lat is None or lon is None:
        return None
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    admin1, country = _text(item.get("admin1")), _text(item.get("country"))
    provider_id = item.get("id")
    stable_id = (
        f"om:{provider_id}"
        if isinstance(provider_id, int) and not isinstance(provider_id, bool)
        else f"{lat:.4f},{lon:.4f}"
    )
    parts = [name]
    for part in (admin1, country):
        if part and part not in parts:
            parts.append(part)
    return {
        "id": stable_id,
        "name": name,
        "admin1": admin1,
        "country": country,
        "country_code": _text(item.get("country_code")),
        "latitude": lat,
        "longitude": lon,
        "timezone": _text(item.get("timezone")),
        "display_name": ", ".join(parts),
    }


def _block(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    block = payload.get(key)
    if not isinstance(block, dict):
        raise WeatherClientError("INVALID_RESPONSE", "The forecast is missing expected data.")
    return block


def _times(block: Mapping[str, Any]) -> list[str]:
    times = block.get("time")
    if not isinstance(times, list) or not all(isinstance(t, str) for t in times):
        raise WeatherClientError("INVALID_RESPONSE", "The forecast has invalid time data.")
    return times


def _series(block: Mapping[str, Any], key: str, length: int) -> list[Any]:
    """Return a list of `length` values; a missing/mismatched series becomes all None."""
    values = block.get(key)
    return values if isinstance(values, list) and len(values) == length else [None] * length


def _unit(units: Any, key: str, kind: str) -> str:
    value = units.get(key) if isinstance(units, dict) else None
    return value if isinstance(value, str) else _DEFAULT_UNITS[kind]


def _normalize_forecast(
    payload: Mapping[str, Any], lat: float, lon: float, location_name: str | None
) -> dict[str, Any]:
    current_raw = _block(payload, "current")
    hourly_raw = _block(payload, "hourly")
    daily_raw = _block(payload, "daily")

    is_day = _flag(current_raw.get("is_day"))
    current: dict[str, Any] = {"time": _text(current_raw.get("time")), "is_day": is_day}
    for out, src in _CURRENT_NUMERIC.items():
        current[out] = _num(current_raw.get(src))
    current["weather"] = describe_weather_code(current_raw.get("weather_code"), is_day)

    h_times = _times(hourly_raw)
    h = {name: _series(hourly_raw, name, len(h_times)) for name in HOURLY_VARIABLES}
    hourly = []
    for i, stamp in enumerate(h_times):
        point: dict[str, Any] = {"time": stamp}
        for out, src in _HOURLY_NUMERIC.items():
            point[out] = _num(h[src][i])
        point["is_day"] = _flag(h["is_day"][i])
        point["weather"] = describe_weather_code(h["weather_code"][i], point["is_day"])
        hourly.append(point)

    d_times = _times(daily_raw)
    d = {name: _series(daily_raw, name, len(d_times)) for name in DAILY_VARIABLES}
    daily = []
    for i, stamp in enumerate(d_times):
        day: dict[str, Any] = {"date": stamp}
        for out, src in _DAILY_NUMERIC.items():
            day[out] = _num(d[src][i])
        for out, src in _DAILY_TEXT.items():
            day[out] = _text(d[src][i])
        day["weather"] = describe_weather_code(d["weather_code"][i], True)
        daily.append(day)

    offset = _num(payload.get("utc_offset_seconds"))
    cu, hu, du = payload.get("current_units"), payload.get("hourly_units"), payload.get("daily_units")
    return {
        "source": "open-meteo",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "location": {
            "name": _text(location_name),
            "latitude": lat,
            "longitude": lon,
            "elevation": _num(payload.get("elevation")),
        },
        "timezone": {
            "id": _text(payload.get("timezone")),
            "abbreviation": _text(payload.get("timezone_abbreviation")),
            "utc_offset_seconds": int(offset) if offset is not None else None,
        },
        "units": {
            "temperature": _unit(cu, "temperature_2m", "temperature"),
            "humidity": _unit(cu, "relative_humidity_2m", "humidity"),
            "precipitation": _unit(cu, "precipitation", "precipitation"),
            "pressure": _unit(cu, "pressure_msl", "pressure"),
            "wind_speed": _unit(cu, "wind_speed_10m", "wind_speed"),
            "wind_direction": _unit(cu, "wind_direction_10m", "wind_direction"),
            "visibility": _unit(cu, "visibility", "visibility"),
            "uv_index": _unit(cu, "uv_index", "uv_index"),
            "snow_depth": _unit(hu, "snow_depth", "snow_depth"),
            "duration": _unit(du, "daylight_duration", "duration"),
        },
        "current": current,
        "hourly": hourly,
        "daily": daily,
    }