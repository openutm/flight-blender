from typing import Protocol, runtime_checkable


@runtime_checkable
class WeatherClient(Protocol):
    async def get_weather(self, longitude: str, latitude: str, time: str, timezone: str) -> dict: ...
