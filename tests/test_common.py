"""
Tests for common utilities: enums, redis_client, redis_stream_operations, and auth/jwt_bearer.
"""

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class TestEnums:
    def test_altitude_ref_values(self):
        from flight_blender.common.enums import AltitudeRef

        assert AltitudeRef.WGS84 == 0
        assert AltitudeRef.AGL == 1
        assert AltitudeRef.MSL == 2
        assert AltitudeRef.W84 == 4

    def test_conformance_state_values(self):
        from flight_blender.common.enums import ConformanceState

        assert ConformanceState.NONCONFORMING == 0
        assert ConformanceState.CONFORMING == 1

    def test_operation_state_values(self):
        from flight_blender.common.enums import OperationState

        assert OperationState.NOT_SUBMITTED == 0
        assert OperationState.ACCEPTED == 1
        assert OperationState.ACTIVATED == 2
        assert OperationState.ENDED == 5

    def test_operation_type_values(self):
        from flight_blender.common.enums import OperationType

        assert OperationType.VLOS == 1
        assert OperationType.BVLOS == 2
        assert OperationType.CREWED == 3

    def test_flight_observation_traffic_source_values(self):
        from flight_blender.common.enums import FlightObservationTrafficSource

        assert FlightObservationTrafficSource.ADSB_UNVALIDATED == 0
        assert FlightObservationTrafficSource.NETWORK_REMOTE_ID == 11
        assert FlightObservationTrafficSource.BROADCAST_REMOTE_ID == 13

    def test_surveillance_sensor_health_constants(self):
        from flight_blender.common.enums import SurveillanceSensorHealth

        assert SurveillanceSensorHealth.OPERATIONAL == "operational"
        assert SurveillanceSensorHealth.DEGRADED == "degraded"
        assert SurveillanceSensorHealth.OUTAGE == "outage"

    def test_surveillance_sensor_maintenance_constants(self):
        from flight_blender.common.enums import SurveillanceSensorMaintenance

        assert SurveillanceSensorMaintenance.PLANNED == "planned"
        assert SurveillanceSensorMaintenance.UNPLANNED == "unplanned"

    def test_scope_constants(self):
        from flight_blender.common.enums import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE, RESPONSE_CONTENT_TYPE

        assert FLIGHTBLENDER_READ_SCOPE == "blender.read"
        assert FLIGHTBLENDER_WRITE_SCOPE == "blender.write"
        assert RESPONSE_CONTENT_TYPE == "application/json"


# ---------------------------------------------------------------------------
# Redis client
# ---------------------------------------------------------------------------
class TestRedisClient:
    def setup_method(self):
        """Reset module-level singletons before each test."""
        import flight_blender.common.redis_client as rc

        rc._async_pool = None
        rc._sync_client = None

    def teardown_method(self):
        """Reset module-level singletons after each test to avoid leaking mocks."""
        import flight_blender.common.redis_client as rc

        rc._async_pool = None
        rc._sync_client = None

    def test_get_async_redis_creates_instance(self):
        mock_redis = MagicMock()
        with patch("flight_blender.common.redis_client.aioredis.Redis", return_value=mock_redis):
            from flight_blender.common.redis_client import get_async_redis

            client = get_async_redis()
            assert client is mock_redis

    def test_get_async_redis_reuses_instance(self):
        mock_redis = MagicMock()
        with patch("flight_blender.common.redis_client.aioredis.Redis", return_value=mock_redis) as mock_cls:
            from flight_blender.common.redis_client import get_async_redis

            c1 = get_async_redis()
            c2 = get_async_redis()
            assert c1 is c2
            mock_cls.assert_called_once()

    def test_get_sync_redis_creates_instance(self):
        mock_redis = MagicMock()
        with patch("flight_blender.common.redis_client.sync_redis.Redis", return_value=mock_redis):
            from flight_blender.common.redis_client import get_redis

            client = get_redis()
            assert client is mock_redis

    def test_get_sync_redis_reuses_instance(self):
        mock_redis = MagicMock()
        with patch("flight_blender.common.redis_client.sync_redis.Redis", return_value=mock_redis) as mock_cls:
            from flight_blender.common.redis_client import get_redis

            c1 = get_redis()
            c2 = get_redis()
            assert c1 is c2
            mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# Redis stream operations
# ---------------------------------------------------------------------------
class TestRedisStreamOperations:
    def test_add_air_traffic_data_calls_xadd(self):
        mock_r = MagicMock()
        mock_r.xadd.return_value = "1234-0"
        with patch("flight_blender.common.redis_stream_operations.get_redis", return_value=mock_r):
            from flight_blender.common.redis_stream_operations import add_air_traffic_data

            result = add_air_traffic_data({"lat_dd": 10.0, "lon_dd": 20.0})
            assert result == "1234-0"
            mock_r.xadd.assert_called_once()

    def test_add_air_traffic_data_filters_none(self):
        mock_r = MagicMock()
        mock_r.xadd.return_value = "1234-0"
        with patch("flight_blender.common.redis_stream_operations.get_redis", return_value=mock_r):
            from flight_blender.common.redis_stream_operations import add_air_traffic_data

            add_air_traffic_data({"lat_dd": 10.0, "lon_dd": None, "alt": 100})
            call_args = mock_r.xadd.call_args[0]
            # None values should be filtered out
            assert "lon_dd" not in call_args[1]
            assert "lat_dd" in call_args[1]

    def test_add_air_traffic_data_json_encodes_dicts(self):
        import json

        mock_r = MagicMock()
        mock_r.xadd.return_value = "1234-0"
        with patch("flight_blender.common.redis_stream_operations.get_redis", return_value=mock_r):
            from flight_blender.common.redis_stream_operations import add_air_traffic_data

            add_air_traffic_data({"metadata": {"callsign": "TEST123"}})
            call_args = mock_r.xadd.call_args[0]
            stored = call_args[1]["metadata"]
            parsed = json.loads(stored)
            assert parsed["callsign"] == "TEST123"

    def test_read_all_observations_returns_fields(self):
        mock_r = MagicMock()
        mock_r.xrevrange.return_value = [("1234-0", {"lat_dd": "10.0", "session_id": "sess1"})]
        with patch("flight_blender.common.redis_stream_operations.get_redis", return_value=mock_r):
            from flight_blender.common.redis_stream_operations import read_all_observations

            result = read_all_observations()
            assert len(result) == 1
            assert result[0]["lat_dd"] == "10.0"

    def test_read_all_observations_filters_by_session_id(self):
        mock_r = MagicMock()
        mock_r.xrevrange.return_value = [
            ("1234-0", {"lat_dd": "10.0", "session_id": "sess1"}),
            ("1234-1", {"lat_dd": "20.0", "session_id": "sess2"}),
        ]
        with patch("flight_blender.common.redis_stream_operations.get_redis", return_value=mock_r):
            from flight_blender.common.redis_stream_operations import read_all_observations

            result = read_all_observations(session_id="sess1")
            assert len(result) == 1
            assert result[0]["session_id"] == "sess1"

    def test_read_latest_observation_returns_first(self):
        mock_r = MagicMock()
        mock_r.xrevrange.return_value = [
            ("1234-0", {"lat_dd": "10.0", "session_id": "s1"}),
            ("1234-1", {"lat_dd": "5.0", "session_id": "s1"}),
        ]
        with patch("flight_blender.common.redis_stream_operations.get_redis", return_value=mock_r):
            from flight_blender.common.redis_stream_operations import read_latest_observation

            result = read_latest_observation()
            assert result["lat_dd"] == "10.0"

    def test_read_latest_observation_returns_none_when_empty(self):
        mock_r = MagicMock()
        mock_r.xrevrange.return_value = []
        with patch("flight_blender.common.redis_stream_operations.get_redis", return_value=mock_r):
            from flight_blender.common.redis_stream_operations import read_latest_observation

            assert read_latest_observation() is None


# ---------------------------------------------------------------------------
# JWT Bearer auth
# ---------------------------------------------------------------------------
class TestJwtBearer:
    """Tests for jwt_bearer module — non-async parts."""

    def test_require_scope_function_is_callable(self):
        from flight_blender.auth.jwt_bearer import require_scope

        dep = require_scope("blender.read")
        assert callable(dep)

    def test_require_scope_returns_unique_dependencies(self):
        from flight_blender.auth.jwt_bearer import require_scope

        dep1 = require_scope("blender.read")
        dep2 = require_scope("blender.write")
        # Each call creates a distinct closure
        assert dep1 is not dep2
