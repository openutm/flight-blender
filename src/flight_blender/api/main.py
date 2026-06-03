from fastapi import FastAPI

from flight_blender.api.routers import geo_fence, weather

MIGRATED_PREFIXES: list[str] = [
    "/geo_fence_ops",
    "/weather_monitoring_ops",
]


def create_fastapi_app() -> FastAPI:
    app = FastAPI(title="Flight Blender")
    # Routers declare NO prefix — asgi.py mount holds it
    app.include_router(geo_fence.router)
    app.include_router(weather.router)
    return app
