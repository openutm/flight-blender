"""
FastAPI application entry point.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from loguru import logger

from flight_blender.config import get_settings
from flight_blender.database import engine
from flight_blender.routers import (
    conformance,
    constraint,
    flight_declaration,
    flight_feed,
    geo_fence,
    rid,
    scd,
    surveillance,
    uss,
    utm_adapter,
    weather,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler. Schema is managed by Alembic migrations."""
    logger.info("Starting Flight Blender …")
    yield
    logger.info("Shutting down Flight Blender …")
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # ── Security middleware ────────────────────────────────────────────────
    if not settings.debug:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else settings.allowed_hosts,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ────────────────────────────────────────────────────────────
    app.include_router(flight_feed.router, prefix="/flight_stream", tags=["Flight Feed"])
    app.include_router(flight_declaration.router, prefix="/flight_declaration_ops", tags=["Flight Declaration"])
    app.include_router(geo_fence.router, prefix="/geo_fence_ops", tags=["Geo Fence"])
    app.include_router(constraint.router, prefix="/constraint_ops", tags=["Constraints"])
    app.include_router(rid.router, prefix="/rid", tags=["Remote ID"])
    app.include_router(scd.router, prefix="/scd", tags=["SCD"])
    app.include_router(uss.router, prefix="/uss_ops", tags=["USS Operations"])
    app.include_router(utm_adapter.router, prefix="/utm_adapter", tags=["UTM Adapter"])
    app.include_router(surveillance.router, prefix="/surveillance_monitoring_ops", tags=["Surveillance"])
    app.include_router(conformance.router, prefix="/conformance_monitoring_ops", tags=["Conformance"])
    app.include_router(weather.router, prefix="/weather_monitoring_ops", tags=["Weather"])

    @app.get("/ping", tags=["Health"])
    async def ping():
        return {"message": "pong"}

    @app.get("/", tags=["Health"])
    async def root():
        return {"message": "Flight Blender is running"}

    return app


app = create_app()
