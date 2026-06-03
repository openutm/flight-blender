import arrow

from flight_blender.infrastructure.external.weather_client import WeatherClient


class WeatherOperations:
    def __init__(self, client: WeatherClient):
        self.client = client

    async def get_weather(
        self,
        longitude: str,
        latitude: str,
        time: str | None = None,
        timezone: str | None = None,
    ) -> dict:
        t = time if time else arrow.now().format("YYYY-MM-DD")
        tz = timezone if timezone else "UTC"
        return await self.client.get_weather(longitude, latitude, t, tz)
