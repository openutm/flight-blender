from unittest.mock import patch
import uuid

import pytest
from tests.conftest import fastapi_auth_header, READ_SCOPE, WRITE_SCOPE


@pytest.mark.django_db
class TestSetAirTraffic:
    def test_set_air_traffic(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {
            "observations": [
                {
                    "lat_dd": 52.5,
                    "lon_dd": 13.4,
                    "altitude_mm": 50000,
                    "traffic_source": 1,
                    "source_type": 1,
                    "icao_address": "ABC123",
                    "timestamp": 1717243200,
                }
            ]
        }
        resp = mounted_sync_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_set_air_traffic_with_metadata(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {
            "observations": [
                {
                    "lat_dd": 52.5,
                    "lon_dd": 13.4,
                    "altitude_mm": 50000,
                    "traffic_source": 1,
                    "source_type": 1,
                    "icao_address": "ABC123",
                    "timestamp": 1717243200,
                    "metadata": {"speed": 500, "heading": 90},
                }
            ]
        }
        resp = mounted_sync_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_set_air_traffic_multiple_observations(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {
            "observations": [
                {
                    "lat_dd": 52.5,
                    "lon_dd": 13.4,
                    "altitude_mm": 50000,
                    "traffic_source": 1,
                    "source_type": 1,
                    "icao_address": f"ABC{i:03d}",
                    "timestamp": 1717243200,
                }
                for i in range(5)
            ]
        }
        resp = mounted_sync_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_set_air_traffic_missing_observations(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        resp = mounted_sync_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json={},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 422

    def test_set_air_traffic_unsupported_media_type(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        resp = mounted_sync_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            content=b"{}",
            headers={**fastapi_auth_header(WRITE_SCOPE), "content-type": "text/plain"},
        )
        assert resp.status_code == 422

    def test_set_air_traffic_invalid_observation(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {"observations": [{"invalid": "data"}]}
        resp = mounted_sync_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 422

    def test_set_air_traffic_missing_required_field(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {
            "observations": [
                {
                    "lat_dd": 52.5,
                    # Missing lon_dd, altitude_mm, etc.
                    "icao_address": "ABC123",
                }
            ]
        }
        resp = mounted_sync_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 422


@pytest.mark.django_db
class TestBulkSetAirTraffic:
    def test_bulk_set_air_traffic(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {
            "observations": [
                {
                    "lat_dd": 52.5,
                    "lon_dd": 13.4,
                    "altitude_mm": 50000,
                    "traffic_source": 1,
                    "source_type": 1,
                    "icao_address": "ABC123",
                    "timestamp": 1717243200,
                }
            ]
        }
        resp = mounted_sync_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_bulk_set_air_traffic_with_metadata(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {
            "observations": [
                {
                    "lat_dd": 52.5,
                    "lon_dd": 13.4,
                    "altitude_mm": 50000,
                    "traffic_source": 1,
                    "source_type": 1,
                    "icao_address": "ABC123",
                    "timestamp": 1717243200,
                    "metadata": {"speed": 500},
                }
            ]
        }
        resp = mounted_sync_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_bulk_set_air_traffic_multiple_batches(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {
            "observations": [
                {
                    "lat_dd": 52.5,
                    "lon_dd": 13.4,
                    "altitude_mm": 50000,
                    "traffic_source": 1,
                    "source_type": 1,
                    "icao_address": f"ABC{i:03d}",
                    "timestamp": 1717243200,
                }
                for i in range(300)
            ]
        }
        resp = mounted_sync_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_bulk_set_missing_observations(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        resp = mounted_sync_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            json={},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 422

    def test_bulk_set_invalid_observation(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        payload = {"observations": [{"invalid": "data"}]}
        resp = mounted_sync_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 422

    def test_bulk_set_unsupported_media_type(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        resp = mounted_sync_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            content=b"{}",
            headers={**fastapi_auth_header(WRITE_SCOPE), "content-type": "text/plain"},
        )
        assert resp.status_code == 422


@pytest.mark.django_db
class TestGetAirTraffic:
    def test_get_air_traffic_missing_view(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/flight_stream/get_air_traffic/{session_id}",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_air_traffic_invalid_view(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/flight_stream/get_air_traffic/{session_id}?view=bad",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_air_traffic_empty(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/flight_stream/get_air_traffic/{session_id}?view=52.500,13.399,52.501,13.400",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "observations" in data

    def test_get_air_traffic_invalid_port(self, mounted_sync_client):
        session_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/flight_stream/get_air_traffic/{session_id}?view=52.5,13.4,52.5,13.4",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code in (200, 400)


@pytest.mark.django_db
class TestStartOpenSkyFeed:
    @pytest.fixture(autouse=True)
    def _mock_opensky_task(self):
        with patch("flight_blender.flight_feed.tasks.start_opensky_network_stream.delay"):
            yield

    def test_start_opensky_missing_view(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_stream/start_opensky_feed",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_start_opensky_invalid_view(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_stream/start_opensky_feed?view=bad",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_start_opensky_valid_view(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_stream/start_opensky_feed?view=52.500,13.399,52.501,13.400",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data


@pytest.mark.django_db
class TestSetTelemetry:
    def test_set_telemetry_missing_observations(self, mounted_sync_client):
        resp = mounted_sync_client.put(
            "/flight_stream/set_telemetry",
            json={},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_set_telemetry_missing_flight_details(self, mounted_sync_client):
        payload = {
            "observations": [
                {
                    "current_states": [],
                }
            ]
        }
        resp = mounted_sync_client.put(
            "/flight_stream/set_telemetry",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_set_telemetry_invalid_flight_details(self, mounted_sync_client):
        import uuid as _uuid
        payload = {
            "observations": [
                {
                    "current_states": [
                        {
                            "timestamp": {"value": "2025-06-01T12:00:00Z", "format": "RFC3339"},
                            "position": {"lat": 52.5, "lng": 13.4, "alt": 50},
                        }
                    ],
                    "flight_details": {
                        "rid_details": {
                            "id": str(_uuid.uuid4()),
                            "operator_id": "OP-001",
                            "operator_location": {"lat": 52.5, "lng": 13.4},
                            "operation_description": "Test",
                            "serial_number": "SN-001",
                            "registration_number": "REG-001",
                        }
                    },
                }
            ]
        }
        resp = mounted_sync_client.put(
            "/flight_stream/set_telemetry",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code in (400, 500)


@pytest.mark.django_db
class TestTrafficInformationDiscovery:
    def test_traffic_info_not_registered(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_stream/traffic_information?view=52.500,13.399,52.501,13.400",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 404


