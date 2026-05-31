"""
FastAPI router for weather monitoring operations.

Mirrors the Django ``WeatherAPIView``: requires the write scope, does
presence-only validation (missing lon/lat -> 400 ``{"error": ...}``), forwards
``time``/``timezone`` to the service, and returns the full upstream object
serialized to the Django ``WeatherSerializer`` shape.
"""

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from loguru import logger

from flight_blender.auth import WriteDep
from flight_blender.config import get_settings
from flight_blender.schemas.weather import WeatherResponse
from flight_blender.services.weather_service import WeatherService

router = APIRouter()
settings = get_settings()


@router.get("/weather/", response_model=WeatherResponse, dependencies=[WriteDep])
async def get_weather(
    latitude: float | None = Query(default=None),
    longitude: float | None = Query(default=None),
    time: float | None = Query(default=None),
    timezone: str = Query(default="UTC"),
):
    # Presence-only validation, matching the Django contract (400 + {"error": ...}).
    if longitude is None:
        return JSONResponse(status_code=400, content={"error": "Longitude parameter is required"})
    if latitude is None:
        return JSONResponse(status_code=400, content={"error": "Latitude parameter is required"})

    service = WeatherService(base_url=settings.weather_api_base_url)
    try:
        data = await service.get_weather(longitude=longitude, latitude=latitude, timezone=timezone, time=time)
    except (httpx.HTTPError, ValueError) as exc:
        # Log the detail but do not leak the upstream response body to the client.
        logger.error(f"Upstream weather service error: {exc}")
        return JSONResponse(status_code=502, content={"error": "Upstream weather service error"})
    return data
