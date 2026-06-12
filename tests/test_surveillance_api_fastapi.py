"""FastAPI tests for surveillance_monitoring_ops endpoints."""
import uuid
from unittest.mock import MagicMock, patch

import jwt
import pytest


def _auth(scopes: list[str]) -> dict[str, str]:
    payload = {
        "sub": "test-user",
        "iss": "dummy",
        "aud": "testflight.flightblender.com",
        "scope": " ".join(scopes),
    }
    token = jwt.encode(payload, "secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


READ_SCOPE = ["flightblender.read"]
WRITE_SCOPE = ["flightblender.write"]


class TestSurveillanceHealthFastAPI:
    def test_health_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/surveillance_monitoring_ops/health/")
        assert resp.status_code == 401

    def test_health_ok(self, fastapi_client):
        resp = fastapi_client.get("/surveillance_monitoring_ops/health/", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "current_status" in data
        assert "sdsp_identifier" in data
        assert "timestamp" in data
        assert data["current_status"] == "outage"  # no sensors → outage


class TestSurveillanceSensorsFastAPI:
    def test_list_sensors_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/surveillance_monitoring_ops/list_surveillance_sensors")
        assert resp.status_code == 401

    def test_list_sensors_empty(self, fastapi_client):
        resp = fastapi_client.get("/surveillance_monitoring_ops/list_surveillance_sensors", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "active_sensors" in data
        assert data["active_sensors"] == []


class TestSurveillanceSessionFastAPI:
    def test_start_session_unauthenticated(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "start"},
        )
        assert resp.status_code == 401

    def test_start_session(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "start"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "Surveillance monitoring heartbeat started"

    def test_start_session_duplicate(self, fastapi_client):
        session_id = str(uuid.uuid4())
        fastapi_client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "start"},
            headers=_auth(WRITE_SCOPE),
        )
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "start"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_stop_session_not_started(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "stop"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_invalid_action(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "invalid"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 422


class TestServiceMetricsFastAPI:
    def test_service_metrics_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/surveillance_monitoring_ops/service_metrics")
        assert resp.status_code == 401

    def test_service_metrics(self, fastapi_client):
        resp = fastapi_client.get("/surveillance_monitoring_ops/service_metrics", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "heartbeat_rates" in data
        assert "active_sessions" in data
        assert "window_start" in data
        assert "window_end" in data

    def test_service_metrics_with_dates(self, fastapi_client):
        resp = fastapi_client.get(
            "/surveillance_monitoring_ops/service_metrics?start_date=2025-01-01&end_date=2025-12-31",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_service_metrics_invalid_date(self, fastapi_client):
        resp = fastapi_client.get(
            "/surveillance_monitoring_ops/service_metrics?start_date=not-a-date",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 400


class TestSensorHealthFastAPI:
    def test_update_sensor_health_unauthenticated(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/update_sensor_health/{sensor_id}",
            json={"status": "operational"},
        )
        assert resp.status_code == 401

    def test_update_sensor_health_not_found(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/update_sensor_health/{sensor_id}",
            json={"status": "operational", "recovery_type": "automatic"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 404

    def test_update_sensor_health_invalid_status(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/update_sensor_health/{sensor_id}",
            json={"status": "invalid_status"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_update_sensor_health_invalid_recovery_type(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/surveillance_monitoring_ops/update_sensor_health/{sensor_id}",
            json={"status": "operational", "recovery_type": "invalid"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400


class TestSensorHealthNotificationsFastAPI:
    def test_list_notifications_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/surveillance_monitoring_ops/list_sensor_health_notifications")
        assert resp.status_code == 401

    def test_list_notifications_empty(self, fastapi_client):
        resp = fastapi_client.get("/surveillance_monitoring_ops/list_sensor_health_notifications", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "notifications" in data
        assert data["notifications"] == []

    def test_list_notifications_with_dates(self, fastapi_client):
        resp = fastapi_client.get(
            "/surveillance_monitoring_ops/list_sensor_health_notifications?start_date=2025-01-01&end_date=2025-12-31",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_notifications_with_sensor_id(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.get(
            f"/surveillance_monitoring_ops/list_sensor_health_notifications?sensor_id={sensor_id}",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notifications"] == []


# ---------------------------------------------------------------------------
# Scheduler additional coverage
# ---------------------------------------------------------------------------


class TestSchedulerCoverage:
    """Additional tests for TaskSchedulerService."""

    def test_schedule_conformance_check_success(self):
        """Test schedule_conformance_check returns True on success."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.app') as mock_app:
            mock_app.send_task = MagicMock()

            result = TaskSchedulerService.schedule_conformance_check(
                flight_declaration_id="test-fd-id",
                session_id="test-session-id",
                expires="2024-12-31T23:59:59",
            )

            assert result is True
            mock_app.send_task.assert_called_once()

    def test_schedule_conformance_check_failure(self):
        """Test schedule_conformance_check returns False on failure."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.app') as mock_app:
            mock_app.send_task.side_effect = Exception("test error")

            result = TaskSchedulerService.schedule_conformance_check(
                flight_declaration_id="test-fd-id",
                session_id="test-session-id",
                expires="2024-12-31T23:59:59",
            )

            assert result is False

    def test_schedule_rid_stream_monitoring_success(self):
        """Test schedule_rid_stream_monitoring returns True on success."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.app') as mock_app:
            mock_app.send_task = MagicMock()

            result = TaskSchedulerService.schedule_rid_stream_monitoring(
                session_id="test-session-id",
                end_datetime="2024-12-31T23:59:59",
            )

            assert result is True
            mock_app.send_task.assert_called_once()

    def test_schedule_rid_stream_monitoring_failure(self):
        """Test schedule_rid_stream_monitoring returns False on failure."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.app') as mock_app:
            mock_app.send_task.side_effect = Exception("test error")

            result = TaskSchedulerService.schedule_rid_stream_monitoring(
                session_id="test-session-id",
                end_datetime="2024-12-31T23:59:59",
            )

            assert result is False

    def test_schedule_surveillance_heartbeat_success(self):
        """Test schedule_surveillance_heartbeat returns True on success."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.app') as mock_app:
            mock_app.send_task = MagicMock()

            result = TaskSchedulerService.schedule_surveillance_heartbeat(
                surveillance_session_id="test-session-id",
            )

            assert result is True
            mock_app.send_task.assert_called_once()

    def test_schedule_surveillance_heartbeat_failure(self):
        """Test schedule_surveillance_heartbeat returns False on failure."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.app') as mock_app:
            mock_app.send_task.side_effect = Exception("test error")

            result = TaskSchedulerService.schedule_surveillance_heartbeat(
                surveillance_session_id="test-session-id",
            )

            assert result is False

    def test_schedule_surveillance_track_success(self):
        """Test schedule_surveillance_track returns True on success."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.app') as mock_app:
            mock_app.send_task = MagicMock()

            result = TaskSchedulerService.schedule_surveillance_track(
                surveillance_session_id="test-session-id",
            )

            assert result is True
            mock_app.send_task.assert_called_once()

    def test_schedule_surveillance_track_failure(self):
        """Test schedule_surveillance_track returns False on failure."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.app') as mock_app:
            mock_app.send_task.side_effect = Exception("test error")

            result = TaskSchedulerService.schedule_surveillance_track(
                surveillance_session_id="test-session-id",
            )

            assert result is False

    def test_cancel_session_tasks(self):
        """Test cancel_session_tasks sets Redis key."""
        from flight_blender.tasks.scheduler import TaskSchedulerService

        with patch('flight_blender.tasks.scheduler.get_redis') as mock_get_redis:
            mock_redis = MagicMock()
            mock_get_redis.return_value = mock_redis

            TaskSchedulerService.cancel_session_tasks(session_id="test-session-id")

            mock_redis.set.assert_called_once_with("stop_task_test-session-id", "1", ex=300)
# Realtime service additional coverage
# ---------------------------------------------------------------------------


class TestRealtimeServiceCoverage:
    """Additional tests for realtime_svc."""

    @pytest.mark.asyncio
    async def test_redis_pubsub_websocket(self):
        """Test redis_pubsub_websocket."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from fastapi import WebSocketDisconnect
        from flight_blender.services.realtime_svc import redis_pubsub_websocket

        mock_websocket = AsyncMock()
        mock_redis_client = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis_client.pubsub = MagicMock(return_value=mock_pubsub)

        async def mock_from_url(*args, **kwargs):
            return mock_redis_client

        with patch('flight_blender.services.realtime_svc.aioredis') as mock_aioredis:
            mock_aioredis.from_url = mock_from_url

            # Mock the listen iterator to raise WebSocketDisconnect
            async def mock_listen():
                yield {"type": "message", "data": "test-message"}
                raise WebSocketDisconnect()

            mock_pubsub.listen = mock_listen

            await redis_pubsub_websocket(mock_websocket, "test-channel")

            mock_websocket.accept.assert_called_once()
            mock_pubsub.subscribe.assert_called_once_with("test-channel")
            mock_pubsub.unsubscribe.assert_called_once_with("test-channel")
