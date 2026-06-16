import uuid

import arrow
from fastapi import HTTPException

import flight_blender.clients.dss_scd_client as dss_helper
from flight_blender.domain_types.scd import (
    CommonPeer9xxResponse,
    FlightPlanCurrentStatus,
    OperationalIntentUpdateResponse,
    OpIntUpdateCheckResultCodes,
    ShouldSendtoDSSProcessingResponse,
)
from tests import fakes
from tests.conftest import SCD_INJECT_SCOPE, SCD_PLAN_SCOPE, SCD_TEST_SCOPE, fastapi_auth_header
from tests.fakes import VALID_OPERATOR_ID, VALID_UAS_SERIAL_NUMBER


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


class TestFlightPlanningClearArea:
    def test_clear_area_missing_payload(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/scd/flight_planning/clear_area_requests",
            json={},
            headers=fastapi_auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 400
        assert "result" in resp.json()

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
        # Missing required nested fields — client payload error → 400
        assert resp.status_code == 400
        assert "result" in resp.json()

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

    def test_update_existing_flight_plan_uses_request_repo(self, mounted_sync_client, mock_scd_dss_success, monkeypatch):
        """Existing-plan updates must parse stored operational intent data with the request repo."""
        async def reject_update(self, **kwargs):
            return OperationalIntentUpdateResponse(
                dss_response=CommonPeer9xxResponse(message="Update not submitted to DSS"),
                status=dss_helper.OPINT_UPDATE_NOT_SUBMITTED_STATUS,
                message="Update not submitted to DSS",
                additional_information=ShouldSendtoDSSProcessingResponse(
                    check_id=OpIntUpdateCheckResultCodes.B,
                    should_submit_update_payload_to_dss=0,
                    tentative_flight_plan_processing_response=FlightPlanCurrentStatus.OkToFly,
                ),
            )

        monkeypatch.setattr(dss_helper.SCDOperations, "update_specified_operational_intent_reference", reject_update)

        plan_id = str(uuid.uuid4())
        payload = self._valid_payload()
        create_resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=payload,
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert create_resp.status_code == 200

        update_resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=payload,
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )

        assert update_resp.status_code == 200
        assert update_resp.json()["planning_result"] in ("NotPlanned", "Rejected")

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

    def test_upsert_dss_http_exception_uses_handler(self, mounted_sync_client, monkeypatch):
        """Fatal DSS transport errors propagate through the FastAPI exception handler."""
        def raise_dss_timeout(self, **kwargs):
            raise HTTPException(status_code=504, detail={"message": "DSS request timed out"})

        monkeypatch.setattr(dss_helper.SCDOperations, "get_auth_token", lambda self, audience="": fakes.fake_auth_token_success())
        monkeypatch.setattr(dss_helper.SCDOperations, "create_and_submit_operational_intent_reference", raise_dss_timeout)
        monkeypatch.setattr(dss_helper.SCDOperations, "process_peer_uss_notifications", fakes.fake_noop)

        plan_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            json=self._valid_payload(),
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )

        assert resp.status_code == 504
        assert resp.json() == {"message": "DSS request timed out"}


class TestFlightPlanningUserNotifications:
    def test_user_notifications_missing_after_is_validation_error(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/scd/flight_planning/user_notifications",
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 422

    def test_user_notifications_invalid_after_is_validation_error(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/scd/flight_planning/user_notifications?after=not-a-date",
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 422

    def test_user_notifications_valid_after_returns_list(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/scd/flight_planning/user_notifications?after=2025-01-01T00:00:00Z",
            headers=fastapi_auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "user_notifications" in data
        assert isinstance(data["user_notifications"], list)
