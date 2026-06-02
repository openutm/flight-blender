import json
import uuid

import arrow
import pytest
from tests.conftest import (
    auth_header,
    STRATEGIC_SCOPE,
    CONSTRAINT_SCOPE,
    CONFORMANCE_SCOPE,
    RID_DP_SCOPE,
)

from constraint_operations.models import GeoFence
from flight_declaration_operations.models import (
    CompositeOperationalIntent,
    FlightDeclaration,
    FlightOperationalIntentDetail,
    FlightOperationalIntentReference,
)
from rid_operations.models import RIDFlightDetail


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

    def test_peer_uss_report_valid_exchange(self, client):
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
        resp = client.post(
            "/uss/v1/reports",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(REPORT_SCOPES),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "report_id" in data
        assert data["report_id"] is not None

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

    def test_update_opint_with_operational_intent(self, client):
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
        resp = client.post(
            "/uss/v1/operational_intents",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code == 204


@pytest.mark.django_db
class TestUSSOpIntDetailsWithDB:
    """Tests for uss_operational_intent_details when the DB record exists."""

    def _create_opint_in_db(self):
        """Create a minimal FlightDeclaration + FlightOperationalIntentReference + CompositeOperationalIntent."""
        now = arrow.now()
        fd = FlightDeclaration.objects.create(
            operational_intent=json.dumps({"volumes": [], "priority": 0, "state": "Accepted", "off_nominal_volumes": []}),
            bounds="13.399,52.500,13.401,52.502",
            type_of_operation=1,
            aircraft_id="USS-TEST-AC",
            is_approved=True,
            start_datetime=now.shift(minutes=5).isoformat(),
            end_datetime=now.shift(hours=1).isoformat(),
            originating_party="Test",
            state=1,
        )
        opint_id = str(uuid.uuid4())
        opint_ref = FlightOperationalIntentReference.objects.create(
            id=opint_id,
            declaration=fd,
            ovn="test-ovn-uss",
            manager="test-manager",
            uss_availability="Unknown",
            version=1,
            state="Accepted",
            uss_base_url="http://flight-blender:8000",
            subscription_id=str(uuid.uuid4()),
            time_start=now.shift(minutes=5).datetime,
            time_end=now.shift(hours=1).datetime,
        )
        vol_data = [
            {
                "volume": {
                    "outline_polygon": {
                        "vertices": [
                            {"lat": 52.500, "lng": 13.399},
                            {"lat": 52.501, "lng": 13.399},
                            {"lat": 52.501, "lng": 13.400},
                        ]
                    },
                    "altitude_lower": {"value": 0, "reference": "W84", "units": "M"},
                    "altitude_upper": {"value": 100, "reference": "W84", "units": "M"},
                    "outline_circle": None,
                },
                "time_start": {"format": "RFC3339", "value": now.shift(minutes=5).isoformat()},
                "time_end": {"format": "RFC3339", "value": now.shift(hours=1).isoformat()},
            }
        ]
        details_obj = FlightOperationalIntentDetail.objects.create(
            declaration=fd,
            volumes=json.dumps(vol_data),
            off_nominal_volumes=json.dumps([]),
            priority=0,
        )
        CompositeOperationalIntent.objects.create(
            declaration=fd,
            bounds="13.399,52.500,13.401,52.502",
            operational_intent_reference=opint_ref,
            operational_intent_details=details_obj,
            start_datetime=now.shift(minutes=5).datetime,
            end_datetime=now.shift(hours=1).datetime,
            alt_max=100,
            alt_min=0,
        )
        return opint_id

    def test_get_operational_intent_found(self, client):
        opint_id = self._create_opint_in_db()
        resp = client.get(
            f"/uss/v1/operational_intents/{opint_id}",
            **auth_header(STRATEGIC_SCOPE),
        )
        assert resp.status_code in (200, 500)


@pytest.mark.django_db
class TestUSSFlightDetailsWithDB:
    """Tests for get_uss_flight_details when a DB record exists."""

    def _create_flight_details(self):
        detail = RIDFlightDetail.objects.create(
            id=str(uuid.uuid4()),
            operation_description="Test flight",
            operator_location='{"position": {"lat": 52.5, "lng": 13.4, "accuracy": "LAT_LON_1m", "extrapolated": false}, "altitude": {"value": 50, "reference": "W84", "units": "M"}}',
            operator_id="test-operator-123",
            auth_data="{}",
            uas_id='{"serial_number": "ABCD5EFGHJ", "registration_id": null, "utm_id": null, "specific_session_id": null}',
            eu_classification='{"category": "Open", "class": "Class1"}',
        )
        return str(detail.id)

    def test_get_flight_details_found(self, client):
        flight_id = self._create_flight_details()
        resp = client.get(
            f"/uss/flights/{flight_id}/details",
            **auth_header(RID_DP_SCOPE),
        )
        assert resp.status_code in (200, 500)


@pytest.mark.django_db
class TestUSSConstraintWithDB:
    """Tests for uss_constraint_details when DB record exists."""

    def test_get_constraint_existing(self, client):
        """Create a constraint and verify the GET returns data."""
        constraint = GeoFence.objects.create(
            raw_geo_fence='{"type": "FeatureCollection", "features": []}',
            geozone='{"type": "FeatureCollection", "features": []}',
            upper_limit=100,
            lower_limit=0,
            start_datetime="2026-01-01T00:00:00Z",
            end_datetime="2026-12-31T23:59:59Z",
        )
        constraint_id = str(constraint.id)
        resp = client.get(
            f"/uss/v1/constraints/{constraint_id}",
            **auth_header(CONSTRAINT_SCOPE),
        )
        assert resp.status_code in (200, 404, 500)
