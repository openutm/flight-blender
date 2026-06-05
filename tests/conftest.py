import fakeredis
import jwt
import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from flight_blender.api.main import create_fastapi_app
from flight_blender.db.session import Base, async_get_db, engine as sync_engine
from flight_blender.models.conformance_orm import ConformanceRecordORM as _NewConformanceORM  # noqa: F401 — triggers new metadata
from flight_blender.models.constraint_orm import ConstraintDetailORM as _NewConstraintORM  # noqa: F401
from flight_blender.models.flight_declarations_orm import FlightDeclarationORM as _NewFlightDeclORM  # noqa: F401
from flight_blender.models.flight_feed_orm import FlightObservationORM as _NewFlightFeedORM  # noqa: F401
from flight_blender.models.geo_fence_orm import GeoFenceORM as _NewGeoFenceORM  # noqa: F401
from flight_blender.models.notifications_orm import OperatorRIDNotificationORM as _NewNotificationsORM  # noqa: F401
from flight_blender.models.rid_orm import ISASubscriptionORM as _NewRIDORM  # noqa: F401
from flight_blender.models.surveillance_orm import SurveillanceSensorORM as _NewSurveillanceORM  # noqa: F401


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


@pytest.fixture(scope="session", autouse=True)
def _sync_sqlalchemy_schema():
    """Create tables for migrated sync code paths that still use session_scope()."""
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    yield
    Base.metadata.drop_all(sync_engine)


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

    import flight_blender.auth.token_cache as auth_redis_helpers

    monkeypatch.setattr(auth_redis_helpers, "get_redis", lambda: fake)
    monkeypatch.setattr(auth_redis_helpers, "get_async_redis", lambda: fake_async)

    _patched_get_redis = lambda: fake
    _patched_get_async_redis = lambda: fake_async

    for _mod_path in (
        # New _api router paths
        "flight_blender.api.routers.flight_feed_api",
        "flight_blender.api.routers.geo_fence_api",
        "flight_blender.api.routers.rid_api",
        "flight_blender.api.routers.uss_api",
        # Old router paths (still exist alongside new ones)
        "flight_blender.api.routers.flight_feed",
        "flight_blender.api.routers.geo_fence",
        "flight_blender.api.routers.rid",
        "flight_blender.api.routers.uss",
        # New task paths
        "flight_blender.tasks.conformance_task",
        "flight_blender.tasks.geo_fence_task",
        "flight_blender.tasks.rid_task",
        "flight_blender.tasks.surveillance_task",
        # Old task paths (still exist)
        "flight_blender.infrastructure.celery.tasks.conformance",
        "flight_blender.infrastructure.celery.tasks.geo_fence",
        "flight_blender.infrastructure.celery.tasks.rid",
        "flight_blender.infrastructure.celery.tasks.surveillance",
        # New client paths
        "flight_blender.clients.dss_rid_client",
        "flight_blender.clients.dss_scd_client",
        # New util paths
        "flight_blender.utils.spatial_flight_declarations",
        "flight_blender.utils.spatial_geo_fence",
        "flight_blender.utils.spatial_rid",
        # Old util paths
        "flight_blender.infrastructure.redis.stream_operations",
        "flight_blender.infrastructure.spatial.flight_declarations",
        "flight_blender.infrastructure.spatial.geo_fence",
        "flight_blender.infrastructure.spatial.rid",
        "flight_blender.infrastructure.dss.rid",
        "flight_blender.infrastructure.dss.scd",
        "flight_blender.auth.pki",
        "flight_blender.auth.dss_auth",
    ):
        try:
            _mod = __import__(_mod_path, fromlist=["*"])
        except ImportError:
            continue
        if hasattr(_mod, "get_redis"):
            monkeypatch.setattr(_mod, "get_redis", _patched_get_redis)
        if hasattr(_mod, "get_async_redis"):
            monkeypatch.setattr(_mod, "get_async_redis", _patched_get_async_redis)

    class FakeRedisWrapper:
        """Stand-in for redis.Redis that delegates to a shared fakeredis."""

        def __new__(cls, *args, **kwargs):
            return fake

    monkeypatch.setattr(redis, "Redis", FakeRedisWrapper)

    return fake


@pytest.fixture
async def mounted_sync_client():
    """Sync FastAPI client serving production-prefixed routes directly."""
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
    import flight_blender.clients.dss_scd_client as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_error())


@pytest.fixture
def mock_scd_dss_success(monkeypatch):
    """SCDOperations succeeds: auth OK, DSS submission accepted."""
    from tests import fakes
    import flight_blender.clients.dss_scd_client as dss_helper

    monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self: fakes.fake_auth_token_success())
    monkeypatch.setattr(
        dss_helper.SCDOperations,
        "create_and_submit_operational_intent_reference",
        lambda self, **kwargs: fakes.fake_submission_success(),
    )
    monkeypatch.setattr(dss_helper.SCDOperations, "process_peer_uss_notifications", fakes.fake_noop)
    monkeypatch.setattr(
        dss_helper.SCDOperations, "get_nearby_operational_intents", lambda self, **kwargs: fakes.fake_empty_nearby_operational_intents()
    )


@pytest.fixture
def mock_scd_dss_conflict(monkeypatch):
    """SCDOperations: auth OK, DSS submission returns conflict."""
    from tests import fakes
    import flight_blender.clients.dss_scd_client as dss_helper

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
    import flight_blender.clients.dss_scd_client as dss_helper

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
    import flight_blender.clients.dss_scd_client as dss_helper

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
    import flight_blender.clients.dss_scd_client as dss_helper

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
    import flight_blender.clients.dss_scd_client as dss_helper

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
    import flight_blender.clients.dss_scd_client as dss_helper

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
