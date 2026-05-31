"""
Weather service — thin wrapper around the Open-Meteo (or similar) forecast API.
"""

import httpx

WEATHER_TOPICS = [
    "temperature_2m",
    "showers",
    "windspeed_10m",
    "winddirection_10m",
    "windgusts_10m",
]


class WeatherService:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def get_weather(self, longitude: float, latitude: float, timezone: str) -> dict:
        params = {
            "longitude": longitude,
            "latitude": latitude,
            "timezone": timezone,
            "forecast_days": "1",
            "hourly": ",".join(WEATHER_TOPICS),
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(self.base_url, params=params)
        if resp.status_code != 200:
            raise ValueError(f"Error fetching weather data: {resp.text}")
        return resp.json()
