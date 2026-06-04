from fastapi import FastAPI

from flight_blender.api.routers import flight_feed, geo_fence, surveillance, weather

MIGRATED_PREFIXES: list[str] = [
    "/geo_fence_ops",
    "/weather_monitoring_ops",
    "/surveillance_monitoring_ops",
    "/flight_stream",
]


def create_fastapi_app() -> FastAPI:
    app = FastAPI(title="Flight Blender")
    # Routers declare NO prefix — asgi.py mount holds it
    app.include_router(geo_fence.router)
    app.include_router(weather.router)
    app.include_router(surveillance.router)
    app.include_router(flight_feed.router)
    return app
