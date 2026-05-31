"""
Weather service — thin async wrapper around the Open-Meteo forecast API.

Mirrors the Django ``services.weather_service.WeatherService``: same upstream
params (including ``time``), the same ``WEATHER_TOPICS``, ``forecast_days``, a
30s request timeout, and a non-200 -> error contract. The full upstream JSON
object is returned unchanged.
"""

import time as _time

import httpx

WEATHER_TOPICS = [
    "temperature_2m",
    "showers",
    "windspeed_10m",
    "winddirection_10m",
    "windgusts_10m",
]

UPSTREAM_TIMEOUT_SECONDS = 30


class WeatherService:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def get_weather(
        self,
        longitude: float,
        latitude: float,
        timezone: str = "UTC",
        time: int | float | str | None = None,
    ) -> dict:
        if time is None:
            time = _time.time()
        params = {
            "longitude": longitude,
            "latitude": latitude,
            "time": time,
            "timezone": timezone,
            "forecast_days": "1",
            "hourly": ",".join(WEATHER_TOPICS),
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(self.base_url, params=params, timeout=UPSTREAM_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            raise ValueError(f"Error fetching weather data: {resp.text}")
        return resp.json()
