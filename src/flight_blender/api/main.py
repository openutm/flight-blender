from fastapi import FastAPI

from flight_blender.api.routers import (
    conformance,
    constraint,
    flight_declarations,
    flight_feed,
    geo_fence,
    misc,
    notifications,
    rid,
    scd,
    surveillance,
    uss,
    weather,
)

MIGRATED_PREFIXES: list[str] = [
    "/geo_fence_ops",
    "/weather_monitoring_ops",
    "/surveillance_monitoring_ops",
    "/flight_stream",
    "/constraint_ops",
    "/notifications_ops",
    "/conformance_monitoring_ops",
    "/rid",
    "/flight_declaration_ops",
    "/scd",
    "/uss",
]


def create_fastapi_app() -> FastAPI:
    app = FastAPI(title="Flight Blender")
    # Routers declare NO prefix — asgi.py mount holds it
    app.include_router(misc.router)
    app.include_router(geo_fence.router)
    app.include_router(weather.router)
    app.include_router(surveillance.router)
    app.include_router(flight_feed.router)
    app.include_router(constraint.router)
    app.include_router(notifications.router)
    app.include_router(conformance.router)
    app.include_router(rid.router)
    app.include_router(flight_declarations.router)
    app.include_router(scd.router)
    app.include_router(uss.router)
    return app
