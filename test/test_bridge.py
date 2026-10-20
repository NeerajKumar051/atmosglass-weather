"""Bridge tests: no network; the service is replaced by a stub."""

from __future__ import annotations

import json

from app.bridge import Api
from app.clients.open_meteo_client import WeatherClientError


class StubService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.last_location = None

    def search_locations(self, query):
        if self.error:
            raise self.error
        return [{"id": "om:1", "name": "Oslo", "country": "Norway", "latitude": 59.91, "longitude": 10.75}]

    def get_weather(self, location):
        if self.error:
            raise self.error
        self.last_location = location
        return {"fetched_at": "2026-01-01T00:00:00+00:00", "current": {}, "hourly": [], "daily": []}


def test_success_envelopes_are_json_safe_and_sanitized():
    stub = StubService()
    api = Api(stub)  # type: ignore[arg-type]
    found = api.search_locations("Oslo")
    assert found["ok"] and found["data"][0]["name"] == "Oslo" and found["meta"]["count"] == 1
    weather = api.get_weather({"latitude": 59.91, "longitude": 10.75, "name": "Oslo", "evil": "x"})
    assert weather["ok"] and weather["meta"]["fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert set(stub.last_location) == {"latitude", "longitude", "name"}  # unknown keys dropped
    json.dumps([found, weather])
    assert api.get_app_info()["message"] == "Python-React bridge connected."


def test_bad_input_and_errors_never_raise():
    api = Api(StubService())  # type: ignore[arg-type]
    for bad in (None, 5, ["x"]):
        assert api.search_locations(bad)["error"]["code"] == "VALIDATION_ERROR"
    for bad in (None, "Oslo", [1]):
        assert api.get_weather(bad)["error"]["code"] == "VALIDATION_ERROR"

    failing = Api(StubService(WeatherClientError("TIMEOUT", "Too slow.", "internal detail")))  # type: ignore[arg-type]
    result = failing.get_weather({"latitude": 1, "longitude": 2})
    assert result["ok"] is False and result["error"] == {"code": "TIMEOUT", "message": "Too slow."}

    crashing = Api(StubService(RuntimeError("boom")))  # type: ignore[arg-type]
    result = crashing.search_locations("Oslo")
    assert result["error"]["code"] == "UNKNOWN_ERROR" and "boom" not in result["error"]["message"]