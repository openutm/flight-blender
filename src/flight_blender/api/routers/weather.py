from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from flight_blender.api.dependencies import require_scopes
from flight_blender.api.schemas.weather import WeatherResponse
from flight_blender.common.data_definitions import FLIGHTBLENDER_READ_SCOPE
from flight_blender.config import settings
from flight_blender.core.operations.weather import WeatherOperations
from flight_blender.infrastructure.external.weather_client import WeatherClient

router = APIRouter()


def _ops() -> WeatherOperations:
    return WeatherOperations(client=WeatherClient(base_url=settings.WEATHER_API_BASE_URL))


@router.get("/weather/", response_model=WeatherResponse)
async def get_weather(
    longitude: str | None = None,
    latitude: str | None = None,
    time: str | None = None,
    timezone: str | None = None,
    ops: WeatherOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    if not longitude:
        return JSONResponse({"error": "Longitude parameter is required"}, status_code=400)
    if not latitude:
        return JSONResponse({"error": "Latitude parameter is required"}, status_code=400)

    weather_data = await ops.get_weather(longitude, latitude, time, timezone)
    return weather_data
