"""FastAPI tests for weather_monitoring_ops endpoints."""
from unittest.mock import AsyncMock, patch

import jwt
import pytest

READ_SCOPE = ["flightblender.read"]


def _fastapi_auth(scopes: list[str]) -> dict[str, str]:
    payload = {
        "sub": "test-user",
        "iss": "dummy",
        "aud": "testflight.flightblender.com",
        "scope": " ".join(scopes),
    }
    token = jwt.encode(payload, "secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


MOCK_WEATHER = {
    "latitude": 52.5,
    "longitude": 13.4,
    "generationtime_ms": 0.1,
    "utc_offset_seconds": 0,
    "timezone": "UTC",
    "timezone_abbreviation": "UTC",
    "elevation": 50.0,
    "hourly_units": {
        "time": "iso8601",
        "temperature_2m": "°C",
        "showers": "mm",
        "windspeed_10m": "km/h",
        "winddirection_10m": "°",
        "windgusts_10m": "km/h",
    },
    "hourly": {
        "time": ["2025-06-01T12:00"],
        "temperature_2m": [20.5],
        "showers": [0.0],
        "windspeed_10m": [10.2],
        "winddirection_10m": [180],
        "windgusts_10m": [15.0],
    },
}


class TestWeatherEndpoint:
    def test_weather_missing_longitude(self, fastapi_client):
        resp = fastapi_client.get("/weather/?latitude=52.5", headers=_fastapi_auth(READ_SCOPE))
        assert resp.status_code == 400
        assert "longitude" in resp.json()["error"].lower()

    def test_weather_missing_latitude(self, fastapi_client):
        resp = fastapi_client.get("/weather/?longitude=13.4", headers=_fastapi_auth(READ_SCOPE))
        assert resp.status_code == 400
        assert "latitude" in resp.json()["error"].lower()

    def test_weather_success(self, fastapi_client):
        with patch("flight_blender.api.routers.weather.WeatherClient") as MockWS:
            instance = MockWS.return_value
            instance.get_weather = AsyncMock(return_value=MOCK_WEATHER)
            resp = fastapi_client.get(
                "/weather/?longitude=13.4&latitude=52.5",
                headers=_fastapi_auth(READ_SCOPE),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "latitude" in data
        assert "hourly" in data

    def test_weather_missing_auth(self, fastapi_client):
        resp = fastapi_client.get("/weather/?longitude=13.4&latitude=52.5")
        assert resp.status_code == 401
