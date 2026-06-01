import json
import uuid

import pytest
from tests.conftest import (
    auth_header,
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


@pytest.mark.django_db
class TestUSSReports:
    def test_peer_uss_report_invalid_payload(self, client):
        payload = {"message": "Test error report"}
        resp = client.post(
            "/uss/v1/reports",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(REPORT_SCOPES),
        )
        assert resp.status_code == 500

    def test_peer_uss_report_unauthenticated(self, client):
        resp = client.post(
            "/uss/v1/reports",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestUSSOperationalIntents:
    def test_get_operational_intent_not_found(self, client):
        opint_id = str(uuid.uuid4())
        resp = client.get(
            f"/uss/v1/operational_intents/{opint_id}",
            **auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code == 404

    def test_update_operational_intent(self, client):
        payload = {
            "operational_intent_id": str(uuid.uuid4()),
            "subscriptions": [],
        }
        resp = client.post(
            "/uss/v1/operational_intents",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code == 204


@pytest.mark.django_db
class TestUSSConstraints:
    def test_get_constraint_not_found(self, client):
        constraint_id = str(uuid.uuid4())
        resp = client.get(
            f"/uss/v1/constraints/{constraint_id}",
            **auth_header(CONSTRAINT_SCOPE),
        )
        assert resp.status_code == 404

    def test_update_constraint_details_missing_constraint(self, client):
        payload = {
            "constraint_id": str(uuid.uuid4()),
            "subscriptions": [],
        }
        resp = client.post(
            "/uss/v1/constraints",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(CONSTRAINT_SCOPE),
        )
        assert resp.status_code == 204


@pytest.mark.django_db
class TestUSSTelemetry:
    def test_get_telemetry(self, client):
        opint_id = str(uuid.uuid4())
        resp = client.get(
            f"/uss/v1/operational_intents/{opint_id}/telemetry",
            **auth_header(CONFORMANCE_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "operational_intent_id" in data
        assert "telemetry" in data
        assert "next_telemetry_opportunity" in data


@pytest.mark.django_db
class TestUSSFlights:
    def test_get_flights_missing_view(self, client):
        resp = client.get(
            "/uss/flights",
            **auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_flights_invalid_view(self, client):
        resp = client.get(
            "/uss/flights?view=bad",
            **auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_flights_view_too_large(self, client):
        resp = client.get(
            "/uss/flights?view=52.0,13.0,53.0,14.0",
            **auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 413

    def test_get_flights_empty(self, client):
        resp = client.get(
            "/uss/flights?view=37.774,-122.420,37.775,-122.419",
            **auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "flights" in data
        assert "timestamp" in data
        assert data["flights"] == []

    def test_get_flight_details_not_found(self, client):
        flight_id = str(uuid.uuid4())
        resp = client.get(
            f"/uss/flights/{flight_id}/details",
            **auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code == 404

    def test_get_flights_unauthenticated(self, client):
        resp = client.get(
            "/uss/flights?view=37.774,-122.420,37.775,-122.419",
        )
        assert resp.status_code == 401

    def test_get_flight_details_unauthenticated(self, client):
        flight_id = str(uuid.uuid4())
        resp = client.get(
            f"/uss/flights/{flight_id}/details",
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestUSSUpdateOpIntDetails:
    def test_update_opint_missing_payload(self, client):
        resp = client.post(
            "/uss/v1/operational_intents",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(STRATEGIC_SCOPE),
        )
        # Missing required fields → dacite error → 500
        assert resp.status_code == 500

    def test_update_opint_invalid_payload(self, client):
        payload = {"operational_intent_id": "not-a-uuid"}
        resp = client.post(
            "/uss/v1/operational_intents",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code == 500
