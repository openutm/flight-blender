import uuid

import arrow
from tests.conftest import (
    fastapi_auth_header,
    STRATEGIC_SCOPE,
    CONSTRAINT_SCOPE,
    CONFORMANCE_SCOPE,
    RID_DP_SCOPE,
)


REPORT_SCOPES = [
    "utm.strategic_coordination",
    "utm.constraint_processing",
    "utm.constraint_management",
    "utm.conformance_monitoring_sa",
    "utm.availability_arbitration",
]


class TestUSSReports:
    def test_peer_uss_report_invalid_payload(self, mounted_sync_client):
        payload = {"message": "Test error report"}
        resp = mounted_sync_client.post(
            "/uss/v1/reports",
            json=payload,
            headers=fastapi_auth_header(REPORT_SCOPES),
        )
        assert resp.status_code == 500

    def test_peer_uss_report_valid_exchange(self, mounted_sync_client):
        """Valid ErrorReport payload → 201 with assigned report_id."""
        now_iso = "2026-06-01T12:00:00Z"
        payload = {
            "report_id": None,
            "exchange": {
                "url": "https://uss.example.com/v1/operational_intents",
                "method": "GET",
                "recorder_role": "Client",
                "request_time": {"value": now_iso, "format": "RFC3339"},
                "response_time": None,
                "problem": "Connection timeout",
                "headers": [],
                "request_body": "",
                "response_body": "",
                "response_code": 0,
            },
        }
        resp = mounted_sync_client.post(
            "/uss/v1/reports",
            json=payload,
            headers=fastapi_auth_header(REPORT_SCOPES),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "report_id" in data
        assert data["report_id"] is not None

    def test_peer_uss_report_unauthenticated(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/uss/v1/reports",
            json={},
        )
        assert resp.status_code == 401


class TestUSSOperationalIntents:
    def test_get_operational_intent_not_found(self, mounted_sync_client):
        opint_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/uss/v1/operational_intents/{opint_id}",
            headers=fastapi_auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code == 404

    def test_update_operational_intent(self, mounted_sync_client):
        payload = {
            "operational_intent_id": str(uuid.uuid4()),
            "subscriptions": [],
        }
        resp = mounted_sync_client.post(
            "/uss/v1/operational_intents",
            json=payload,
            headers=fastapi_auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code == 204


class TestUSSConstraints:
    def test_get_constraint_not_found(self, mounted_sync_client):
        constraint_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/uss/v1/constraints/{constraint_id}",
            headers=fastapi_auth_header(CONSTRAINT_SCOPE),
        )
        assert resp.status_code == 404

    def test_update_constraint_details_missing_constraint(self, mounted_sync_client):
        payload = {
            "constraint_id": str(uuid.uuid4()),
            "subscriptions": [],
        }
        resp = mounted_sync_client.post(
            "/uss/v1/constraints",
            json=payload,
            headers=fastapi_auth_header(CONSTRAINT_SCOPE),
        )
        assert resp.status_code == 204


class TestUSSTelemetry:
    def test_get_telemetry(self, mounted_sync_client):
        opint_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/uss/v1/operational_intents/{opint_id}/telemetry",
            headers=fastapi_auth_header(CONFORMANCE_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "operational_intent_id" in data
        assert "telemetry" in data
        assert "next_telemetry_opportunity" in data


class TestUSSFlights:
    def test_get_flights_missing_view(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/uss/flights",
            headers=fastapi_auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_flights_invalid_view(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/uss/flights?view=bad",
            headers=fastapi_auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_flights_view_too_large(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/uss/flights?view=52.0,13.0,53.0,14.0",
            headers=fastapi_auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 413

    def test_get_flights_empty(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/uss/flights?view=37.774,-122.420,37.775,-122.419",
            headers=fastapi_auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "flights" in data
        assert "timestamp" in data
        assert data["flights"] == []

    def test_get_flight_details_not_found(self, mounted_sync_client):
        flight_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/uss/flights/{flight_id}/details",
            headers=fastapi_auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 404

    def test_get_flights_unauthenticated(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/uss/flights?view=37.774,-122.420,37.775,-122.419",
        )
        assert resp.status_code == 401

    def test_get_flight_details_unauthenticated(self, mounted_sync_client):
        flight_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/uss/flights/{flight_id}/details",
        )
        assert resp.status_code == 401


class TestUSSUpdateOpIntDetails:
    def test_update_opint_missing_payload(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/uss/v1/operational_intents",
            json={},
            headers=fastapi_auth_header(STRATEGIC_SCOPE),
        )
        # Missing required fields → dacite error → 500
        assert resp.status_code == 500

    def test_update_opint_invalid_payload(self, mounted_sync_client):
        payload = {"operational_intent_id": "not-a-uuid"}
        resp = mounted_sync_client.post(
            "/uss/v1/operational_intents",
            json=payload,
            headers=fastapi_auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code == 500

    def test_update_opint_with_operational_intent(self, mounted_sync_client):
        """Provide a full operational_intent body → DB write → 204."""
        start_iso = arrow.now().shift(minutes=5).isoformat()
        end_iso = arrow.now().shift(hours=1).isoformat()
        volume4d = {
            "volume": {
                "outline_polygon": {
                    "vertices": [
                        {"lat": 52.500, "lng": 13.399},
                        {"lat": 52.501, "lng": 13.399},
                        {"lat": 52.501, "lng": 13.400},
                        {"lat": 52.500, "lng": 13.400},
                    ]
                },
                "altitude_lower": {"value": 0, "reference": "W84", "units": "M"},
                "altitude_upper": {"value": 100, "reference": "W84", "units": "M"},
                "outline_circle": None,
            },
            "time_start": {"value": start_iso, "format": "RFC3339"},
            "time_end": {"value": end_iso, "format": "RFC3339"},
        }
        op_int_id = str(uuid.uuid4())
        payload = {
            "operational_intent_id": op_int_id,
            "subscriptions": [],
            "operational_intent": {
                "reference": {
                    "id": op_int_id,
                    "manager": "uss.example.com",
                    "uss_availability": "Unknown",
                    "version": 1,
                    "state": "Accepted",
                    "ovn": "ovn-test-value",
                    "time_start": {"value": start_iso, "format": "RFC3339"},
                    "time_end": {"value": end_iso, "format": "RFC3339"},
                    "uss_base_url": "https://uss.example.com",
                    "subscription_id": str(uuid.uuid4()),
                },
                "details": {
                    "volumes": [volume4d],
                    "priority": 0,
                    "off_nominal_volumes": [],
                },
            },
        }
        resp = mounted_sync_client.post(
            "/uss/v1/operational_intents",
            json=payload,
            headers=fastapi_auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code == 204

