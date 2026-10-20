"""pywebview JavaScript bridge: the only Python surface React can call.

Every public method (no leading underscore) is exposed to JavaScript. Methods return
JSON-safe envelopes {ok, data, meta, error} and never raise into the UI.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.clients.open_meteo_client import WeatherClientError
from app.services.weather_service import WeatherService

logger = logging.getLogger(__name__)

APP_NAME = "AtmosGlass Weather"
APP_VERSION = "0.1.0"

_MAX_NAME_LENGTH = 100


def _ok(data: Any, **meta: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "data": data,
        "meta": {"cached": False, "stale": False, "fetched_at": None, **meta},
        "error": None,
    }


def _fail(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "meta": {"cached": False, "stale": False, "fetched_at": None},
        "error": {"code": code, "message": message},
    }


class Api:
    def __init__(self, service: WeatherService | None = None) -> None:
        self._service = service or WeatherService()  # private: not exposed to JavaScript

    # Phase 1B method: unchanged ------------------------------------------------
    def get_app_info(self) -> dict[str, str]:
        return {
            "name": APP_NAME,
            "version": APP_VERSION,
            "message": "Python-React bridge connected.",
        }

    # Phase 3 methods -----------------------------------------------------------
    def search_locations(self, query: Any) -> dict[str, Any]:
        if not isinstance(query, str):
            return _fail("VALIDATION_ERROR", "Enter a city name.")

        def run() -> dict[str, Any]:
            results = self._service.search_locations(query)
            return _ok(results, count=len(results))

        return self._guard("search_locations", run)

    def get_weather(self, location: Any) -> dict[str, Any]:
        if not isinstance(location, dict):
            return _fail("VALIDATION_ERROR", "The selected location is not valid.")
        # Pass on only the fields the service needs.
        clean: dict[str, Any] = {
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
        }
        for key in ("name", "display_name"):
            value = location.get(key)
            if isinstance(value, str):
                clean[key] = value[:_MAX_NAME_LENGTH]

        def run() -> dict[str, Any]:
            data = self._service.get_weather(clean)
            return _ok(data, fetched_at=data.get("fetched_at"))

        return self._guard("get_weather", run)

    # Internal helper (underscore = not exposed) ---------------------------------
    @staticmethod
    def _guard(name: str, run: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return run()
        except WeatherClientError as err:
            logger.warning("%s failed: %s (%s)", name, err.code, err.reason or "no detail")
            return _fail(err.code, err.message)
        except Exception:  # noqa: BLE001 - nothing may crash the desktop window
            logger.exception("Unexpected error in %s", name)
            return _fail("UNKNOWN_ERROR", "Something went wrong. Please try again.")