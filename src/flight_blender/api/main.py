from fastapi import FastAPI

from flight_blender.api.routers import (
    conformance_api,
    constraint_api,
    flight_declarations_api,
    flight_feed_api,
    geo_fence_api,
    misc_api,
    notifications_api,
    realtime_api,
    rid_api,
    scd_api,
    surveillance_api,
    uss_api,
    versioning_api,
    weather_api,
)


def create_fastapi_app() -> FastAPI:
    app = FastAPI(title="Flight Blender")
    app.include_router(misc_api.router)
    app.include_router(geo_fence_api.router)
    app.include_router(weather_api.router)
    app.include_router(surveillance_api.router)
    app.include_router(flight_feed_api.router)
    app.include_router(constraint_api.router)
    app.include_router(notifications_api.router)
    app.include_router(realtime_api.router)
    app.include_router(conformance_api.router)
    app.include_router(rid_api.router)
    app.include_router(flight_declarations_api.router)
    app.include_router(scd_api.router)
    app.include_router(uss_api.router)
    app.include_router(versioning_api.router)
    return app
