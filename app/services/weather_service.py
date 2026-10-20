"""Weather service: validates a selected location and asks the client for its forecast."""

from __future__ import annotations

from typing import Any, Mapping

from app.clients.open_meteo_client import (
    OpenMeteoClient,
    WeatherClientError,
    validate_coordinates,
    validate_query,
)


class WeatherService:
    def __init__(self, client: OpenMeteoClient | None = None) -> None:
        self._client = client or OpenMeteoClient()

    def search_locations(self, query: Any) -> list[dict[str, Any]]:
        """City search -> [{id, name, admin1, country, latitude, longitude, ...}]."""
        return self._client.search_locations(query)

    def get_weather(self, location: Any) -> dict[str, Any]:
        """Forecast for a selected location dict (e.g. one returned by search_locations)."""
        if not isinstance(location, Mapping):
            raise WeatherClientError("VALIDATION_ERROR", "The selected location is not valid.")
        lat, lon = validate_coordinates(location.get("latitude"), location.get("longitude"))
        raw_name = location.get("display_name") or location.get("name")
        name = validate_query(raw_name) if raw_name is not None else None
        return self._client.get_forecast(lat, lon, name or None)