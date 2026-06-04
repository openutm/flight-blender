from fastapi import FastAPI

from flight_blender.api.routers import (
    conformance,
    constraint,
    flight_declarations,
    flight_feed,
    geo_fence,
    misc,
    notifications,
    realtime,
    rid,
    scd,
    surveillance,
    uss,
    weather,
)


def create_fastapi_app() -> FastAPI:
    app = FastAPI(title="Flight Blender")
    app.include_router(misc.router)
    app.include_router(geo_fence.router)
    app.include_router(weather.router)
    app.include_router(surveillance.router)
    app.include_router(flight_feed.router)
    app.include_router(constraint.router)
    app.include_router(notifications.router)
    app.include_router(realtime.router)
    app.include_router(conformance.router)
    app.include_router(rid.router)
    app.include_router(flight_declarations.router)
    app.include_router(scd.router)
    app.include_router(uss.router)
    return app
