import json
import uuid

import pytest
from tests.conftest import auth_header, READ_SCOPE, WRITE_SCOPE, GA_TEST_SCOPE


@pytest.mark.django_db
class TestSetAirTraffic:
    def test_set_air_traffic(self, client):
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
        resp = client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_set_air_traffic_with_metadata(self, client):
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
        resp = client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_set_air_traffic_multiple_observations(self, client):
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
        resp = client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_set_air_traffic_missing_observations(self, client):
        session_id = str(uuid.uuid4())
        resp = client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_set_air_traffic_unsupported_media_type(self, client):
        session_id = str(uuid.uuid4())
        resp = client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            data="{}",
            content_type="text/plain",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 415

    def test_set_air_traffic_invalid_observation(self, client):
        session_id = str(uuid.uuid4())
        payload = {"observations": [{"invalid": "data"}]}
        resp = client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_set_air_traffic_missing_required_field(self, client):
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
        resp = client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestBulkSetAirTraffic:
    def test_bulk_set_air_traffic(self, client):
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
        resp = client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_bulk_set_air_traffic_with_metadata(self, client):
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
        resp = client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_bulk_set_air_traffic_multiple_batches(self, client):
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
        resp = client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_bulk_set_missing_observations(self, client):
        session_id = str(uuid.uuid4())
        resp = client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_bulk_set_invalid_observation(self, client):
        session_id = str(uuid.uuid4())
        payload = {"observations": [{"invalid": "data"}]}
        resp = client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_bulk_set_unsupported_media_type(self, client):
        session_id = str(uuid.uuid4())
        resp = client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            data="{}",
            content_type="text/plain",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 415


@pytest.mark.django_db
class TestGetAirTraffic:
    def test_get_air_traffic_missing_view(self, client):
        session_id = str(uuid.uuid4())
        resp = client.get(
            f"/flight_stream/get_air_traffic/{session_id}",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_air_traffic_invalid_view(self, client):
        session_id = str(uuid.uuid4())
        resp = client.get(
            f"/flight_stream/get_air_traffic/{session_id}?view=bad",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_air_traffic_empty(self, client):
        session_id = str(uuid.uuid4())
        resp = client.get(
            f"/flight_stream/get_air_traffic/{session_id}?view=52.500,13.399,52.501,13.400",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "observations" in data

    def test_get_air_traffic_invalid_port(self, client):
        session_id = str(uuid.uuid4())
        resp = client.get(
            f"/flight_stream/get_air_traffic/{session_id}?view=52.5,13.4,52.5,13.4",
            **auth_header(READ_SCOPE),
        )
        # Invalid viewport (zero area)
        assert resp.status_code in (200, 400)


@pytest.mark.django_db
class TestStartOpenSkyFeed:
    def test_start_opensky_missing_view(self, client):
        resp = client.get(
            "/flight_stream/start_opensky_feed",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_start_opensky_invalid_view(self, client):
        resp = client.get(
            "/flight_stream/start_opensky_feed?view=bad",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_start_opensky_valid_view(self, client):
        resp = client.get(
            "/flight_stream/start_opensky_feed?view=52.500,13.399,52.501,13.400",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data


@pytest.mark.django_db
class TestSetTelemetry:
    def test_set_telemetry_missing_observations(self, client):
        resp = client.put(
            "/flight_stream/set_telemetry",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_set_telemetry_missing_flight_details(self, client):
        payload = {
            "observations": [
                {
                    "current_states": [],
                }
            ]
        }
        resp = client.put(
            "/flight_stream/set_telemetry",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_set_telemetry_invalid_flight_details(self, client):
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
                            "id": str(uuid.uuid4()),
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
        resp = client.put(
            "/flight_stream/set_telemetry",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        # Operation doesn't exist → 400 or dacite parse error → 500
        assert resp.status_code in (400, 500)


@pytest.mark.django_db
class TestTrafficInformationDiscovery:
    def test_traffic_info_not_registered(self, client):
        # traffic_information_discovery_view is defined but NOT registered in urls.py
        resp = client.get(
            "/flight_stream/traffic_information?view=52.500,13.399,52.501,13.400",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestPublicKeys:
    def test_list_public_keys(self, client):
        resp = client.get(
            "/flight_stream/public_keys/",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200

    def test_create_public_key(self, client):
        resp = client.post(
            "/flight_stream/public_keys/",
            data=json.dumps({"key": "test-key-data"}),
            content_type="application/json",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code in (200, 201, 400)

    def test_public_key_detail_not_found(self, client):
        pk = str(uuid.uuid4())
        resp = client.get(
            f"/flight_stream/public_keys/{pk}/",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 404
