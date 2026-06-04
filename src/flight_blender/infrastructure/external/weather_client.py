import httpx

WEATHER_TOPICS = [
    "temperature_2m",
    "showers",
    "windspeed_10m",
    "winddirection_10m",
    "windgusts_10m",
]


class WeatherClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def get_weather(self, longitude: str, latitude: str, time: str, timezone: str) -> dict:
        params = {
            "longitude": longitude,
            "latitude": latitude,
            "time": time,
            "timezone": timezone,
            "forecast_days": "1",
            "hourly": ",".join(WEATHER_TOPICS),
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(self.base_url, params=params, timeout=30)

        response.raise_for_status()
        return response.json()
