"""
Shared pytest fixtures for UTM integration tests.

- In-memory SQLite database (StaticPool) – fast and isolated.
- Module-level engine patching so the app lifespan uses the same DB.
- Per-test session with automatic rollback for test isolation.
- Celery task mocking to avoid needing a running broker.
- Redis stream operation mocking to avoid needing a running Redis.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
def anyio_backend():
    """Use asyncio backend for each async test."""
    return "asyncio"


@pytest.fixture
async def test_engine():
    """
    Function-scoped in-memory SQLite engine.

    A fresh engine (and therefore fresh in-memory database) is created for
    every test, ensuring complete isolation.  The module-level ``engine`` and
    ``AsyncSessionLocal`` inside ``flight_blender.database`` are patched so
    that both the FastAPI lifespan and ``get_db`` use the test database.
    """
    import flight_blender.database as db_module
    import flight_blender.models  # noqa: F401 – registers all ORM models with Base
    from flight_blender.database import Base

    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    original_engine = db_module.engine
    original_factory = db_module.AsyncSessionLocal
    db_module.engine = engine
    db_module.AsyncSessionLocal = session_factory

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    db_module.engine = original_engine
    db_module.AsyncSessionLocal = original_factory
    await engine.dispose()


@pytest.fixture
async def db(test_engine):
    """Function-scoped DB session; rolls back after each test."""
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(db):
    """
    Async HTTP test client backed by the FastAPI app.

    - Overrides ``get_db`` with the per-test session.
    - Mocks all Celery task ``.delay()`` calls.
    - Mocks Redis stream operations.
    """
    from flight_blender.database import get_db
    from flight_blender.main import create_app

    app = create_app()

    async def override_get_db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("flight_blender.routers.flight_feed.write_incoming_air_traffic_data") as _mock_write,
        patch("flight_blender.routers.flight_feed.bulk_write_incoming_air_traffic_data") as _mock_bulk,
        patch("flight_blender.routers.flight_feed.send_operational_update_message") as _mock_feed_notify,
        patch("flight_blender.routers.flight_declaration.submit_flight_declaration_to_dss_async") as _mock_dss,
        patch("flight_blender.routers.geo_fence.write_geo_zone") as _mock_wgz,
        patch("flight_blender.routers.geo_fence.download_geozone_source") as _mock_dl,
        patch("flight_blender.routers.rid.submit_dss_subscription") as _mock_sub,
        patch("flight_blender.routers.flight_feed.read_all_observations", return_value=[]),
        patch("flight_blender.routers.uss.read_all_observations", return_value=[]),
        patch("flight_blender.routers.surveillance.send_heartbeat_to_consumer") as _mock_hb,
        patch("flight_blender.routers.surveillance.send_and_generate_track_to_consumer") as _mock_track,
        patch("flight_blender.tasks.flight_feed.write_incoming_air_traffic_data") as _mock_utm_write,
        patch(
            "flight_blender.services.weather_service.WeatherService.get_weather",
            new_callable=AsyncMock,
            # Django parity: the upstream Open-Meteo object is returned as-is and
            # serialized to the WeatherSerializer shape (no synthetic
            # ``current_weather`` field; lat/lon come from the upstream response).
            return_value={
                "latitude": 51.5,
                "longitude": -0.1,
                "generationtime_ms": 0.123,
                "utc_offset_seconds": 0,
                "timezone": "UTC",
                "timezone_abbreviation": "GMT",
                "elevation": 25.0,
                "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
                "hourly": {"time": ["2026-05-30T00:00"], "temperature_2m": [12.3]},
            },
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
