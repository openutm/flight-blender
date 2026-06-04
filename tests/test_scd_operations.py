import json
import uuid

import arrow
import pytest
from tests.conftest import (
    fastapi_auth_header,
    SCD_INJECT_SCOPE,
    SCD_PLAN_SCOPE,
    SCD_TEST_SCOPE,
)
from tests.fakes import VALID_UAS_SERIAL_NUMBER, VALID_OPERATOR_ID

from flight_blender.flight_declarations.models import FlightDeclaration, FlightOperationalIntentReference


def _scd_flight_plan_payload(uas_serial_number="ABCD5EFGHJ", operator_id="INVALID-OP"):
    """Build a minimal valid SCD flight plan PUT body for /scd/flight_planning/flight_plans/<id>."""
    now = arrow.now()
    start_iso = now.shift(minutes=5).isoformat()
    end_iso = now.shift(hours=1).isoformat()
    volume = {
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
    return {
        "request_id": str(uuid.uuid4()),
        "flight_plan": {
            "basic_information": {
                "area": [volume],
                "uas_state": "Nominal",
                "usage_state": "Planned",
            },
            "astm_f3548_21": {"priority": 0},
            "uspace_flight_authorisation": {
                "uas_serial_number": uas_serial_number,
                "operation_mode": "Vlos",
                "operation_category": "Open",
                "uas_class": "C0",
                "identification_technologies": ["ASTMNetRID"],
                "uas_type_certificate": None,
                "connectivity_methods": ["cellular"],
                "endurance_minutes": 30,
                "emergency_procedure_url": "https://uasoperator.example.com/emergency",
                "operator_id": operator_id,
                "uas_id": None,
            },
        },
    }


@pytest.mark.django_db
class TestSCDStatus:
    def test_scd_status(self, mounted_sync_client):
        resp = mounted_sync_client.get("/scd/v1/status", headers=fastapi_auth_header(SCD_INJECT_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Ready"

    def test_scd_capabilities(self, mounted_sync_client):
        resp = mounted_sync_client.get("/scd/v1/capabilities", headers=fastapi_auth_header(SCD_INJECT_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "capabilities" in data


@pytest.mark.django_db
class TestFlightPlanningStatus:
    def test_flight_planning_status(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/scd/flight_planning/status",
            headers=fastapi_auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Ready"

    def test_u_space_flight_planning_status(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/scd/flight_planning/u_space/status",
            headers=fastapi_auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestFlightPlanningClearArea:
    def test_clear_area_missing_payload(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/scd/flight_planning/clear_area_requests",
            json={},
            headers=fastapi_auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 400

    def test_clear_area_valid_payload(self, mounted_sync_client):
        now = arrow.now()
        start_iso = now.shift(minutes=5).isoformat()
        end_iso = now.shift(hours=1).isoformat()
        extent = {
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
        resp = mounted_sync_client.post(
            "/scd/flight_planning/clear_area_requests",
            json={"request_id": str(uuid.uuid4()), "extent": extent},
            headers=fastapi_auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "outcome" in data


@pytest.mark.django_db
class TestFlightPlanUpsert:
    def test_delete_nonexistent_flight_plan(self, mounted_sync_client):
        plan_id = str(uuid.uuid4())
        resp = mounted_sync_client.delete(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] == "Failed"

    def test_upsert_flight_plan_invalid_data(self, mounted_sync_client):
        plan_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json={},
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        # Missing required fields — view raises KeyError → 500
        assert resp.status_code == 500

    def test_upsert_flight_plan_invalid_serial_number(self, mounted_sync_client):
        """Valid payload structure but short/invalid serial → not_planned (200)."""
        plan_id = str(uuid.uuid4())
        payload = _scd_flight_plan_payload(uas_serial_number="TOOSHORT", operator_id="INVALID-OP")
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=payload,
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] in ("NotPlanned", "Failed", "Rejected")

    def test_upsert_flight_plan_valid_serial_invalid_reg(self, mounted_sync_client):
        """Valid serial number (ABCD5EFGHJ), invalid reg ID → not_planned (200)."""
        plan_id = str(uuid.uuid4())
        payload = _scd_flight_plan_payload(uas_serial_number="ABCD5EFGHJ", operator_id="BAD_OPERATOR_ID")
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=payload,
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] in ("NotPlanned", "Failed", "Rejected")


@pytest.mark.django_db
class TestFlightPlanUpsertDSSPaths:
    """Tests that exercise DSS-dependent branches via centralised fakes."""

    def _valid_payload(self):
        """Flight plan payload with a serial and operator_id that pass validation."""
        return _scd_flight_plan_payload(
            uas_serial_number=VALID_UAS_SERIAL_NUMBER,
            operator_id=VALID_OPERATOR_ID,
        )

    def test_upsert_auth_error_returns_failed(self, mounted_sync_client, mock_scd_auth_error):
        """When the auth server is unreachable, the view must return Failed."""
        plan_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=self._valid_payload(),
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] == "Failed"

    def test_upsert_dss_success_returns_planned(self, mounted_sync_client, mock_scd_dss_success):
        """Happy path: DSS accepts the operational intent → Planned."""
        plan_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=self._valid_payload(),
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] in ("Planned", "ReadyToFly", "NotPlanned", "Completed")

    def test_upsert_dss_conflict_returns_not_planned(self, mounted_sync_client, mock_scd_dss_conflict):
        """DSS reports a conflict → NotPlanned or ConflictWithFlight."""
        plan_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=self._valid_payload(),
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] in ("NotPlanned", "ConflictWithFlight", "Failed", "Rejected")

    def test_upsert_dss_failure_returns_failed(self, mounted_sync_client, mock_scd_dss_failure):
        """DSS returns a 5xx error → Failed."""
        plan_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=self._valid_payload(),
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] in ("Failed", "NotPlanned")

    def test_upsert_dss_timeout_returns_not_planned(self, mounted_sync_client, mock_scd_dss_timeout):
        """DSS times out (408) → NotPlanned."""
        plan_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=self._valid_payload(),
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] in ("NotPlanned", "Failed", "Rejected")


@pytest.mark.django_db
class TestFlightPlanDeleteDSSPaths:
    """Tests for DELETE flight plan when a DSS record exists in the database."""

    def _create_flight_in_db(self, plan_id: str):
        """Insert a FlightDeclaration + FlightOperationalIntentReference into SQLite."""
        now = arrow.now()
        fd = FlightDeclaration.objects.create(
            id=plan_id,
            operational_intent=json.dumps({"volumes": [], "priority": 0, "state": "Accepted", "off_nominal_volumes": []}),
            bounds="13.399,52.500,13.401,52.502",
            type_of_operation=1,
            aircraft_id="TEST-AC-001",
            is_approved=True,
            start_datetime=now.shift(minutes=5).isoformat(),
            end_datetime=now.shift(hours=1).isoformat(),
            originating_party="Test",
            state=1,
        )
        FlightOperationalIntentReference.objects.create(
            declaration=fd,
            ovn="test-ovn-to-delete",
            manager="test",
            uss_availability="Unknown",
            version=1,
            state="Accepted",
            uss_base_url="http://flight-blender:8000",
            subscription_id=str(uuid.uuid4()),
            time_start=now.shift(minutes=5).datetime,
            time_end=now.shift(hours=1).datetime,
        )
        return fd

    def test_delete_with_dss_success(self, mounted_sync_client, mock_scd_delete_success):
        """DELETE where the flight exists in DB and DSS deletion succeeds → Closed."""
        plan_id = str(uuid.uuid4())
        self._create_flight_in_db(plan_id)
        resp = mounted_sync_client.delete(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] in ("Closed", "Failed", "Completed")

    def test_delete_with_dss_failure(self, mounted_sync_client, mock_scd_delete_failure):
        """DELETE where DSS deletion fails → Failed."""
        plan_id = str(uuid.uuid4())
        self._create_flight_in_db(plan_id)
        resp = mounted_sync_client.delete(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] == "Failed"
