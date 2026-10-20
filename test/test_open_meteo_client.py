"""Offline tests: a fake session replaces all network access."""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from app.clients import open_meteo_client as om
from app.clients.open_meteo_client import OpenMeteoClient, WeatherClientError
from app.services.weather_service import WeatherService
from app.utils.weather_codes import describe_weather_code


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None, bad_json: bool = False) -> None:
        self.status_code = status
        self._payload = payload
        self._bad_json = bad_json

    def json(self) -> Any:
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Returns/raises queued items in order; records every call."""

    def __init__(self, *queue: Any) -> None:
        self.queue = list(queue)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: Any = None, timeout: Any = None) -> Any:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(*queue: Any) -> tuple[OpenMeteoClient, FakeSession, list[float]]:
    session = FakeSession(*queue)
    sleeps: list[float] = []
    return OpenMeteoClient(session=session, sleep=sleeps.append), session, sleeps  # type: ignore[arg-type]


# Hand-written fixture shaped like the documented response (test data, not live weather).
FORECAST_FIXTURE = {
    "latitude": 26.9, "longitude": 75.8, "elevation": 431.0,
    "timezone": "Asia/Kolkata", "timezone_abbreviation": "GMT+5:30", "utc_offset_seconds": 19800,
    "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
    "current": {
        "time": "2026-01-01T23:45", "temperature_2m": 20.5, "is_day": 0,
        "weather_code": 0, "wind_speed_10m": 7.2, "uv_index": None,
    },
    "hourly": {
        "time": ["2026-01-01T23:00", "2026-01-02T00:00"],  # crosses midnight
        "temperature_2m": [21.0, 20.0],
        "weather_code": [3, 999],  # 999 = unknown code
        "is_day": [0, 0],
        # other hourly series intentionally missing -> must become None
    },
    "daily": {
        "time": ["2026-01-01", "2026-01-02"],
        "weather_code": [61, 95],
        "temperature_2m_max": [24.0, None],
        "temperature_2m_min": [11.0, 12.0],
        "sunrise": ["2026-01-01T07:14", "2026-01-02T07:14"],
        "sunset": ["2026-01-01T17:45", "2026-01-02T17:46"],
        "moonrise": [None, "2026-01-02T19:30"],  # null on days without a moonrise
        "rain_sum": [1.2, 0.0],
    },
}


def test_weather_code_mapping_and_fallback():
    assert describe_weather_code(0)["label"] == "Clear sky"
    assert describe_weather_code(0, is_day=False)["icon"] == "moon"
    assert describe_weather_code(96)["label"] == "Thunderstorm with hail"
    assert describe_weather_code(63)["scene"] == "rain"
    for bad in (None, 999, "3", True, float("nan"), 2.5):
        result = describe_weather_code(bad)
        assert result["label"] == "Conditions unavailable"
        assert result["icon"] == "unknown" and result["scene"] == "neutral"


def test_get_forecast_normalizes_and_handles_missing_data():
    client, session, _ = make_client(FakeResponse(payload=FORECAST_FIXTURE))
    data = client.get_forecast(26.9, 75.8, "Jaipur")

    assert set(data) >= {"location", "timezone", "units", "current", "hourly", "daily"}
    assert data["location"]["name"] == "Jaipur"
    assert data["timezone"] == {"id": "Asia/Kolkata", "abbreviation": "GMT+5:30", "utc_offset_seconds": 19800}
    assert data["units"]["temperature"] == "°C" and data["units"]["pressure"] == "hPa"  # default fallback
    assert data["current"]["temperature"] == 20.5
    assert data["current"]["uv_index"] is None and data["current"]["humidity"] is None
    assert data["current"]["weather"]["icon"] == "moon"  # clear + night
    assert [h["time"] for h in data["hourly"]] == ["2026-01-01T23:00", "2026-01-02T00:00"]
    assert data["hourly"][1]["weather"]["label"] == "Conditions unavailable"
    assert data["hourly"][0]["wind_speed"] is None
    assert data["daily"][1]["temperature_max"] is None and data["daily"][1]["weather"]["scene"] == "thunderstorm"
    assert data["daily"][0]["moonrise"] is None and data["daily"][1]["moonrise"] == "2026-01-02T19:30"
    assert data["daily"][0]["rain_sum"] == 1.2 and data["daily"][0]["wind_gusts_max"] is None
    assert data["skipped_variables"] == []
    json.dumps(data)  # must be JSON-serializable

    call = session.calls[0]
    assert call["url"] == om.FORECAST_URL
    assert call["params"]["timezone"] == "auto" and call["params"]["forecast_days"] == 7
    assert call["timeout"] == (om.CONNECT_TIMEOUT, om.READ_TIMEOUT)


def test_search_locations_validation_and_normalization():
    results = {"results": [
        {"id": 1269515, "name": "Jaipur", "latitude": 26.9, "longitude": 75.8,
         "country": "India", "admin1": "Rajasthan", "timezone": "Asia/Kolkata"},
        {"name": "Bad entry without coordinates"},
    ]}
    client, session, _ = make_client(FakeResponse(payload=results), FakeResponse(payload={"generationtime_ms": 0.1}),
                                     FakeResponse(payload={"results": [{"name": "x"}]}))

    assert client.search_locations("  Jaipur ")[0]["display_name"] == "Jaipur, Rajasthan, India"
    assert session.calls[0]["params"]["name"] == "Jaipur"
    assert client.search_locations("Nowhereville") == []  # API omits "results"
    with pytest.raises(WeatherClientError) as exc:
        client.search_locations("Broken")  # only malformed entries
    assert exc.value.code == "INVALID_RESPONSE"

    before = len(session.calls)
    assert client.search_locations("   ") == [] and client.search_locations("a") == []
    assert len(session.calls) == before  # no request for empty/1-char input
    for bad in (None, 42, "x" * 101, "bad\x00name"):
        with pytest.raises(WeatherClientError) as exc:
            client.search_locations(bad)
        assert exc.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "queue, expected_code, expected_calls",
    [
        ([requests.exceptions.ReadTimeout()] * 3, "TIMEOUT", 3),  # bounded retries
        ([requests.exceptions.ConnectionError()] * 3, "NETWORK_ERROR", 3),
        ([FakeResponse(503)] * 3, "PROVIDER_ERROR", 3),
        ([FakeResponse(429)], "RATE_LIMITED", 1),  # never retried
        ([FakeResponse(400, {"error": True, "reason": "bad"})], "PROVIDER_ERROR", 1),
        ([FakeResponse(bad_json=True)], "INVALID_RESPONSE", 1),
        ([FakeResponse(payload=[1, 2])], "INVALID_RESPONSE", 1),
        ([FakeResponse(payload={"latitude": 1})], "INVALID_RESPONSE", 1),  # missing blocks
    ],
)
def test_failures_map_to_error_codes(queue, expected_code, expected_calls):
    client, session, sleeps = make_client(*queue)
    with pytest.raises(WeatherClientError) as exc:
        client.get_forecast(10, 10)
    assert exc.value.code == expected_code
    assert len(session.calls) == expected_calls
    assert len(sleeps) == max(0, expected_calls - 1)


def test_transient_failure_then_success_and_coordinate_validation():
    client, session, sleeps = make_client(FakeResponse(503), FakeResponse(payload=FORECAST_FIXTURE))
    assert client.get_forecast(26.9, 75.8)["current"]["temperature"] == 20.5
    assert len(session.calls) == 2 and sleeps == [om.BACKOFF_SECONDS]
    for lat, lon in ((91, 0), (0, 181), (float("nan"), 0), ("1", 2), (True, 0)):
        with pytest.raises(WeatherClientError):
            client.get_forecast(lat, lon)


def _reject(variable: str, block: str) -> FakeResponse:
    reason = f"Cannot initialize ForecastVariable from invalid String value {variable} for key {block}"
    return FakeResponse(400, {"error": True, "reason": reason})


def test_unsupported_optional_variable_is_dropped_but_core_is_not():
    client, session, _ = make_client(_reject("moonrise", "daily"), FakeResponse(payload=FORECAST_FIXTURE))
    data = client.get_forecast(26.9, 75.8)
    assert data["skipped_variables"] == ["daily.moonrise"]
    assert "moonrise" in session.calls[0]["params"]["daily"]
    assert "moonrise" not in session.calls[1]["params"]["daily"]  # repeated without it

    client, session, _ = make_client(_reject("temperature_2m", "hourly"))
    with pytest.raises(WeatherClientError) as exc:
        client.get_forecast(26.9, 75.8)
    assert exc.value.code == "PROVIDER_ERROR" and len(session.calls) == 1  # core: no retry


def test_service_passes_selected_coordinates_dynamically():
    found = {"results": [{"id": 1, "name": "Oslo", "latitude": 59.91, "longitude": 10.75, "country": "Norway"}]}
    client, session, _ = make_client(FakeResponse(payload=found), FakeResponse(payload=FORECAST_FIXTURE))
    service = WeatherService(client)

    location = service.search_locations("Oslo")[0]
    assert (location["name"], location["country"], location["latitude"]) == ("Oslo", "Norway", 59.91)
    data = service.get_weather(location)
    params = session.calls[1]["params"]
    assert (params["latitude"], params["longitude"]) == (59.91, 10.75)  # from the selection, not hardcoded
    assert data["location"]["name"] == "Oslo, Norway"
    for bad in (None, "Oslo", {}, {"latitude": 1}, {"latitude": 95, "longitude": 0}):
        with pytest.raises(WeatherClientError) as exc:
            service.get_weather(bad)
        assert exc.value.code == "VALIDATION_ERROR"