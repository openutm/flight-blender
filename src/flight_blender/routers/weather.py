"""
FastAPI router for weather monitoring operations.
"""

from fastapi import APIRouter, HTTPException, Query, status

from flight_blender.auth import ReadDep
from flight_blender.config import get_settings
from flight_blender.schemas.weather import WeatherResponse
from flight_blender.services.weather_service import WeatherService

router = APIRouter()
settings = get_settings()


@router.get("/weather/", response_model=WeatherResponse, dependencies=[ReadDep])
async def get_weather(
    latitude: float = Query(..., ge=-90.0, le=90.0),
    longitude: float = Query(..., ge=-180.0, le=180.0),
    timezone: str = Query(default="UTC"),
):
    service = WeatherService(base_url=settings.weather_api_base_url)
    try:
        data = await service.get_weather(longitude=longitude, latitude=latitude, timezone=timezone)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Weather service error: {exc}") from exc
    return WeatherResponse(latitude=latitude, longitude=longitude, current_weather=data.get("current_weather"), hourly=data.get("hourly"))
