"""FastAPI tests for surveillance_monitoring_ops endpoints."""
import uuid

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
        resp = fastapi_client.get("/health/")
        assert resp.status_code == 401

    def test_health_ok(self, fastapi_client):
        resp = fastapi_client.get("/health/", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "current_status" in data
        assert "sdsp_identifier" in data
        assert "timestamp" in data
        assert data["current_status"] == "outage"  # no sensors → outage


class TestSurveillanceSensorsFastAPI:
    def test_list_sensors_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/list_surveillance_sensors")
        assert resp.status_code == 401

    def test_list_sensors_empty(self, fastapi_client):
        resp = fastapi_client.get("/list_surveillance_sensors", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "active_sensors" in data
        assert data["active_sensors"] == []


class TestSurveillanceSessionFastAPI:
    def test_start_session_unauthenticated(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "start"},
        )
        assert resp.status_code == 401

    def test_start_session(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "start"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "Surveillance monitoring heartbeat started"

    def test_start_session_duplicate(self, fastapi_client):
        session_id = str(uuid.uuid4())
        fastapi_client.put(
            f"/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "start"},
            headers=_auth(WRITE_SCOPE),
        )
        resp = fastapi_client.put(
            f"/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "start"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_stop_session_not_started(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "stop"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_invalid_action(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/start_stop_surveillance_heartbeat_track/{session_id}",
            json={"action": "invalid"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 422


class TestServiceMetricsFastAPI:
    def test_service_metrics_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/service_metrics")
        assert resp.status_code == 401

    def test_service_metrics(self, fastapi_client):
        resp = fastapi_client.get("/service_metrics", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "heartbeat_rates" in data
        assert "active_sessions" in data
        assert "window_start" in data
        assert "window_end" in data

    def test_service_metrics_with_dates(self, fastapi_client):
        resp = fastapi_client.get(
            "/service_metrics?start_date=2025-01-01&end_date=2025-12-31",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_service_metrics_invalid_date(self, fastapi_client):
        resp = fastapi_client.get(
            "/service_metrics?start_date=not-a-date",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 400


class TestSensorHealthFastAPI:
    def test_update_sensor_health_unauthenticated(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/update_sensor_health/{sensor_id}",
            json={"status": "operational"},
        )
        assert resp.status_code == 401

    def test_update_sensor_health_not_found(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/update_sensor_health/{sensor_id}",
            json={"status": "operational", "recovery_type": "automatic"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 404

    def test_update_sensor_health_invalid_status(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/update_sensor_health/{sensor_id}",
            json={"status": "invalid_status"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_update_sensor_health_invalid_recovery_type(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.put(
            f"/update_sensor_health/{sensor_id}",
            json={"status": "operational", "recovery_type": "invalid"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400


class TestSensorHealthNotificationsFastAPI:
    def test_list_notifications_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/list_sensor_health_notifications")
        assert resp.status_code == 401

    def test_list_notifications_empty(self, fastapi_client):
        resp = fastapi_client.get("/list_sensor_health_notifications", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "notifications" in data
        assert data["notifications"] == []

    def test_list_notifications_with_dates(self, fastapi_client):
        resp = fastapi_client.get(
            "/list_sensor_health_notifications?start_date=2025-01-01&end_date=2025-12-31",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_notifications_with_sensor_id(self, fastapi_client):
        sensor_id = str(uuid.uuid4())
        resp = fastapi_client.get(
            f"/list_sensor_health_notifications?sensor_id={sensor_id}",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notifications"] == []
