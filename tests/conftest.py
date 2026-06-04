import fakeredis
import jwt
import pytest
import redis
from django.test import Client
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from flight_blender.api.main import create_fastapi_app
from flight_blender.infrastructure.database.models.constraint import (  # noqa: F401 — triggers metadata
    CompositeConstraintORM,
    ConstraintDetailORM,
    ConstraintReferenceORM,
)
from flight_blender.infrastructure.database.models.conformance import ConformanceRecordORM  # noqa: F401 — triggers metadata
from flight_blender.infrastructure.database.models.flight_declarations import (  # noqa: F401 — triggers metadata
    CompositeOperationalIntentORM,
    FlightDeclarationORM,
    FlightOperationalIntentDetailORM,
    FlightOperationalIntentReferenceORM,
    FlightOperationTrackingORM,
    PeerCompositeOperationalIntentORM,
    PeerOperationalIntentDetailORM,
    PeerOperationalIntentReferenceORM,
    SubscriberORM,
)
from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM, SignedTelmetryPublicKeyORM  # noqa: F401 — triggers metadata
from flight_blender.infrastructure.database.models.geo_fence import GeoFenceORM  # noqa: F401 — triggers metadata
from flight_blender.infrastructure.database.models.notifications import OperatorRIDNotificationORM  # noqa: F401 — triggers metadata
from flight_blender.infrastructure.database.models.surveillance import (  # noqa: F401 — triggers metadata
    SurveillanceHeartbeatEventORM,
    SurveillanceSensorFailureNotificationORM,
    SurveillanceSensorHealthORM,
    SurveillanceSensorHealthTrackingORM,
    SurveillanceSensorORM,
    SurveillanceSessionORM,
    SurveillanceTrackEventORM,
)
from flight_blender.infrastructure.database.session import Base, async_get_db


# ── Auth token helpers ───────────────────────────────────────────────────────


def _make_token(scopes: list[str]) -> str:
    """Create an unsigned JWT with the given scopes for bypass-auth testing."""
    payload = {
        "sub": "test-user",
        "iss": "dummy",
        "aud": "testflight.flightblender.com",
        "scope": " ".join(scopes),
    }
    return jwt.encode(payload, "secret", algorithm="HS256")


def auth_header(scopes: list[str]) -> dict:
    """Return an Authorization header dict for the given scopes."""
    return {"HTTP_AUTHORIZATION": f"Bearer {_make_token(scopes)}"}


def fastapi_auth_header(scopes: list[str]) -> dict[str, str]:
    """Return an Authorization header dict for use with FastAPI TestClient (httpx)."""
    return {"Authorization": f"Bearer {_make_token(scopes)}"}


# ── Scope presets ────────────────────────────────────────────────────────────

READ_SCOPE = ["flightblender.read"]
WRITE_SCOPE = ["flightblender.write"]
READ_WRITE_SCOPE = ["flightblender.read", "flightblender.write"]
DSS_READ_SCOPE = ["dss.read.identification_service_areas"]
DSS_WRITE_SCOPE = ["dss.write.identification_service_areas"]
RID_INJECT_SCOPE = ["rid.inject_test_data"]
RID_DP_SCOPE = ["rid.display_provider"]
SCD_INJECT_SCOPE = ["utm.inject_test_data"]
SCD_PLAN_SCOPE = ["interuss.flight_planning.plan"]
SCD_TEST_SCOPE = ["interuss.flight_planning.direct_automated_test"]
GA_TEST_SCOPE = ["geo-awareness.test"]
STRATEGIC_SCOPE = ["utm.strategic_coordination"]
CONSTRAINT_SCOPE = ["utm.constraint_processing"]
CONFORMANCE_SCOPE = ["utm.conformance_monitoring_sa"]


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _celery_eager(monkeypatch):
    """Configure Celery to run tasks synchronously during tests.

    Patches the Celery app configuration so tasks execute eagerly
    (inline, no broker needed).
    """
    from flight_blender.celery import app as celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.eager_propagates_exceptions = False
    celery_app.conf.broker_url = "memory://"
    celery_app.conf.result_backend = "cache+memory://"


class _AsyncRedisAdapter:
    """Wraps a sync fakeredis instance so its methods can be awaited."""

    def __init__(self, r):
        self._r = r

    async def exists(self, *a, **kw):
        return self._r.exists(*a, **kw)

    async def get(self, *a, **kw):
        return self._r.get(*a, **kw)

    async def set(self, *a, **kw):
        return self._r.set(*a, **kw)

    async def expire(self, *a, **kw):
        return self._r.expire(*a, **kw)


@pytest.fixture(autouse=True)
def _mock_all_redis(monkeypatch):
    """Mock all Redis connections to use fakeredis.

    Patches auth_helper.common.get_redis and redis.Redis so that any code
    creating a Redis connection gets a fakeredis instance. Also patches
    get_async_redis with an async-compatible wrapper over the same store.
    """
    server = fakeredis.FakeServer()
    fake = fakeredis.FakeRedis(server=server, decode_responses=True)
    fake_async = _AsyncRedisAdapter(fake)

    import flight_blender.auth.common

    monkeypatch.setattr(flight_blender.auth.common, "get_redis", lambda: fake)
    monkeypatch.setattr(flight_blender.auth.common, "get_async_redis", lambda: fake_async)

    import flight_blender.core.operations.geo_fence as _geo_fence_ops

    monkeypatch.setattr(_geo_fence_ops, "get_async_redis", lambda: fake_async)

    class FakeRedisWrapper:
        """Stand-in for redis.Redis that delegates to a shared fakeredis."""

        def __new__(cls, *args, **kwargs):
            return fake

    monkeypatch.setattr(redis, "Redis", FakeRedisWrapper)

    return fake


@pytest.fixture
def client():
    """Django test client for integration tests."""
    return Client(raise_request_exception=False)


@pytest.fixture
async def mounted_sync_client(transactional_db):  # transactional_db: ordering guard + ensures committed writes are visible to ASGI thread
    """Sync FastAPI client serving production-prefixed routes directly.

    Uses `transactional_db` so Django ORM writes in the test are committed and
    visible to the ASGI handler thread spawned by TestClient.
    """
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_fastapi_app()
    app.dependency_overrides[async_get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    await test_engine.dispose()


@pytest.fixture
async def fastapi_client():
    """FastAPI test client backed by an in-memory async SQLite database."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_fastapi_app()
    app.dependency_overrides[async_get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    await test_engine.dispose()


@pytest.fixture
async def mounted_fastapi_client():
    """FastAPI client that serves production-prefixed routes directly."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fastapi_app = create_fastapi_app()
    fastapi_app.dependency_overrides[async_get_db] = override_get_db
    with TestClient(fastapi_app, raise_server_exceptions=True) as c:
        yield c

    await test_engine.dispose()


@pytest.fixture
def fakeredis_server(_mock_all_redis):
    """Expose the fakeredis server for direct manipulation in tests."""
    return _mock_all_redis


@pytest.fixture
def sample_geojson_feature_collection():
    """A minimal valid GeoJSON FeatureCollection for flight declarations / geo-fences."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [13.4, 52.5],
                            [13.41, 52.5],
                            [13.41, 52.51],
                            [13.4, 52.51],
                            [13.4, 52.5],
                        ]
                    ],
                },
                "properties": {
                    "min_altitude": {"meters": 20, "datum": "WGS84"},
                    "max_altitude": {"meters": 50, "datum": "WGS84"},
                    "upper_limit": 50,
                    "lower_limit": 20,
                    "name": "Test GeoFence",
                },
            }
        ],
    }


@pytest.fixture
def future_dates():
    """Return (start_iso, end_iso) in the near future suitable for flight declarations."""
    import arrow

    now = arrow.now()
    return (
        now.shift(hours=1).isoformat(),
        now.shift(hours=2).isoformat(),
    )


# ---------------------------------------------------------------------------
# DSS / SCDOperations mock fixtures (all backed by tests/fakes.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_scd_auth_error(monkeypatch):
    """SCDOperations.get_auth_token returns an error — triggers the auth-failure branch."""
    from tests import fakes
    import flight_blender.scd.dss_scd_helper as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_error())


@pytest.fixture
def mock_scd_dss_success(monkeypatch):
    """SCDOperations succeeds: auth OK, DSS submission accepted."""
    from tests import fakes
    import flight_blender.scd.dss_scd_helper as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_success())
    monkeypatch.setattr(
        dss_helper.SCDOperations,
        "create_and_submit_operational_intent_reference",
        lambda self, **kwargs: fakes.fake_submission_success(),
    )
    monkeypatch.setattr(dss_helper.SCDOperations, "process_peer_uss_notifications", fakes.fake_noop)
    monkeypatch.setattr(dss_helper.SCDOperations, "get_nearby_operational_intents", lambda self, **kwargs: fakes.fake_empty_nearby_operational_intents())


@pytest.fixture
def mock_scd_dss_conflict(monkeypatch):
    """SCDOperations: auth OK, DSS submission returns conflict."""
    from tests import fakes
    import flight_blender.scd.dss_scd_helper as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_success())
    monkeypatch.setattr(
        dss_helper.SCDOperations,
        "create_and_submit_operational_intent_reference",
        lambda self, **kwargs: fakes.fake_submission_conflict(),
    )
    monkeypatch.setattr(dss_helper.SCDOperations, "process_peer_uss_notifications", fakes.fake_noop)


@pytest.fixture
def mock_scd_dss_failure(monkeypatch):
    """SCDOperations: auth OK, DSS submission fails (500)."""
    from tests import fakes
    import flight_blender.scd.dss_scd_helper as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_success())
    monkeypatch.setattr(
        dss_helper.SCDOperations,
        "create_and_submit_operational_intent_reference",
        lambda self, **kwargs: fakes.fake_submission_failure(),
    )
    monkeypatch.setattr(dss_helper.SCDOperations, "process_peer_uss_notifications", fakes.fake_noop)


@pytest.fixture
def mock_scd_dss_timeout(monkeypatch):
    """SCDOperations: auth OK, DSS submission times out (408)."""
    from tests import fakes
    import flight_blender.scd.dss_scd_helper as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_success())
    monkeypatch.setattr(
        dss_helper.SCDOperations,
        "create_and_submit_operational_intent_reference",
        lambda self, **kwargs: fakes.fake_submission_timeout(),
    )
    monkeypatch.setattr(dss_helper.SCDOperations, "process_peer_uss_notifications", fakes.fake_noop)


@pytest.fixture
def mock_scd_delete_success(monkeypatch):
    """SCDOperations.delete_operational_intent returns success (200)."""
    from tests import fakes
    import flight_blender.scd.dss_scd_helper as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_success())
    monkeypatch.setattr(
        dss_helper.SCDOperations,
        "delete_operational_intent",
        lambda self, **kwargs: fakes.fake_delete_success(),
    )


@pytest.fixture
def mock_scd_delete_failure(monkeypatch):
    """SCDOperations.delete_operational_intent returns failure (404)."""
    from tests import fakes
    import flight_blender.scd.dss_scd_helper as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_success())
    monkeypatch.setattr(
        dss_helper.SCDOperations,
        "delete_operational_intent",
        lambda self, **kwargs: fakes.fake_delete_failure(),
    )


@pytest.fixture
def mock_network_opint_empty(monkeypatch):
    """SCDOperations.get_and_process_nearby_operational_intents returns empty FeatureCollection."""
    from tests import fakes
    import flight_blender.scd.dss_scd_helper as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_success())
    monkeypatch.setattr(
        dss_helper.SCDOperations,
        "get_and_process_nearby_operational_intents",
        lambda self, **kwargs: {"type": "FeatureCollection", "features": []},
    )


# ---------------------------------------------------------------------------
# Shared payload fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def flight_declaration_payload(sample_geojson_feature_collection, future_dates):
    """A complete flight declaration creation payload."""
    start, end = future_dates
    return {
        "originating_party": "Test Operator",
        "start_datetime": start,
        "end_datetime": end,
        "flight_declaration_geo_json": sample_geojson_feature_collection,
        "type_of_operation": 1,
        "aircraft_id": "TEST-UAV-001",
    }


@pytest.fixture
def operational_intent_payload(future_dates):
    """A complete operational intent creation payload (volume4D format)."""
    start, end = future_dates
    return {
        "originating_party": "Test Operator",
        "start_datetime": start,
        "end_datetime": end,
        "operational_intent_volume4ds": [
            {
                "time_start": {"value": start, "format": "RFC3339"},
                "time_end": {"value": end, "format": "RFC3339"},
                "volume": {
                    "outline_circle": {
                        "center": {"lat": 47.6062, "lng": -122.3321},
                        "radius": {"value": 500, "units": "M"},
                    },
                    "altitude_lower": {"value": 20, "reference": "W84", "units": "M"},
                    "altitude_upper": {"value": 50, "reference": "W84", "units": "M"},
                },
            }
        ],
        "type_of_operation": 1,
        "aircraft_id": "TEST-UAV-002",
    }
