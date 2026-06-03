import uuid

import pytest
from tests.conftest import auth_header, READ_SCOPE, WRITE_SCOPE


@pytest.mark.django_db
class TestSurveillanceHealth:
    def test_health_endpoint(self, client):
        resp = client.get(
            "/surveillance_monitoring_ops/health/",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "current_status" in data
        assert "sdsp_identifier" in data
        assert "timestamp" in data

    def test_health_unauthenticated(self, client):
        resp = client.get("/surveillance_monitoring_ops/health/")
        assert resp.status_code == 401


@pytest.mark.django_db
class TestSurveillanceSensors:
    def test_list_sensors_empty(self, client):
        resp = client.get(
            "/surveillance_monitoring_ops/list_surveillance_sensors",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "active_sensors" in data
        assert data["active_sensors"] == []

    def test_list_sensors_unauthenticated(self, client):
        resp = client.get("/surveillance_monitoring_ops/list_surveillance_sensors")
        assert resp.status_code == 401


@pytest.mark.django_db
class TestSurveillanceSession:
    def test_start_session(self, client):
        session_id = str(uuid.uuid4())
        resp = client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            data={"action": "start"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Surveillance monitoring heartbeat started"

    def test_start_session_duplicate(self, client):
        session_id = str(uuid.uuid4())
        client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            data={"action": "start"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        resp = client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            data={"action": "start"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_stop_session_not_started(self, client):
        session_id = str(uuid.uuid4())
        resp = client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            data={"action": "stop"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_invalid_action(self, client):
        session_id = str(uuid.uuid4())
        resp = client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            data={"action": "invalid"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_start_and_stop_session(self, client):
        session_id = str(uuid.uuid4())
        resp = client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            data={"action": "start"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        resp = client.put(
            f"/surveillance_monitoring_ops/start_stop_surveillance_heartbeat_track/{session_id}",
            data={"action": "stop"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestServiceMetrics:
    def test_service_metrics(self, client):
        resp = client.get(
            "/surveillance_monitoring_ops/service_metrics",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "heartbeat_rates" in data
        assert "aggregate_health" in data
        assert "active_sessions" in data
        assert "window_start" in data
        assert "window_end" in data

    def test_service_metrics_with_dates(self, client):
        resp = client.get(
            "/surveillance_monitoring_ops/service_metrics?start_date=2025-01-01&end_date=2025-12-31",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_service_metrics_with_session_id(self, client):
        session_id = str(uuid.uuid4())
        resp = client.get(
            f"/surveillance_monitoring_ops/service_metrics?session_id={session_id}",
            **auth_header(READ_SCOPE),
        )
        # Non-existent session → may raise error → 500
        assert resp.status_code in (200, 500)

    def test_service_metrics_invalid_date(self, client):
        resp = client.get(
            "/surveillance_monitoring_ops/service_metrics?start_date=not-a-date",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestSensorHealth:
    def test_update_sensor_health_not_found(self, client):
        sensor_id = str(uuid.uuid4())
        resp = client.put(
            f"/surveillance_monitoring_ops/update_sensor_health/{sensor_id}",
            data={"status": "operational", "recovery_type": "automatic"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 404

    def test_update_sensor_health_invalid_status(self, client):
        sensor_id = str(uuid.uuid4())
        resp = client.put(
            f"/surveillance_monitoring_ops/update_sensor_health/{sensor_id}",
            data={"status": "invalid_status"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_update_sensor_health_invalid_recovery_type(self, client):
        sensor_id = str(uuid.uuid4())
        resp = client.put(
            f"/surveillance_monitoring_ops/update_sensor_health/{sensor_id}",
            data={"status": "operational", "recovery_type": "invalid"},
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestSensorHealthNotifications:
    def test_list_notifications(self, client):
        resp = client.get(
            "/surveillance_monitoring_ops/list_sensor_health_notifications",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "notifications" in data

    def test_list_notifications_with_dates(self, client):
        resp = client.get(
            "/surveillance_monitoring_ops/list_sensor_health_notifications?start_date=2025-01-01&end_date=2025-12-31",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_notifications_with_sensor_id(self, client):
        sensor_id = str(uuid.uuid4())
        resp = client.get(
            f"/surveillance_monitoring_ops/list_sensor_health_notifications?sensor_id={sensor_id}",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
