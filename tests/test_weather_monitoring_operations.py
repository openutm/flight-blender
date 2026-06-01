from unittest.mock import patch

import pytest
from tests.conftest import auth_header, WRITE_SCOPE


@pytest.mark.django_db
class TestWeatherEndpoint:
    def test_weather_missing_longitude(self, client):
        resp = client.get(
            "/weather_monitoring_ops/weather/?latitude=52.5",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400
        assert "longitude" in resp.json()["error"].lower()

    def test_weather_missing_latitude(self, client):
        resp = client.get(
            "/weather_monitoring_ops/weather/?longitude=13.4",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400
        assert "latitude" in resp.json()["error"].lower()

    def test_weather_success(self, client):
        mock_weather_data = {
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
        with patch("weather_monitoring_operations.views.WeatherService") as MockWS:
            mock_instance = MockWS.return_value
            mock_instance.get_weather.return_value = mock_weather_data
            resp = client.get(
                "/weather_monitoring_ops/weather/?longitude=13.4&latitude=52.5",
                **auth_header(WRITE_SCOPE),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "latitude" in data
            assert "hourly" in data
