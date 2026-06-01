import json
import uuid

import pytest
from tests.conftest import (
    auth_header,
    SCD_INJECT_SCOPE,
    SCD_PLAN_SCOPE,
    SCD_TEST_SCOPE,
)


@pytest.mark.django_db
class TestSCDStatus:
    def test_scd_status(self, client):
        resp = client.get("/scd/v1/status", **auth_header(SCD_INJECT_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Ready"

    def test_scd_capabilities(self, client):
        resp = client.get("/scd/v1/capabilities", **auth_header(SCD_INJECT_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "capabilities" in data


@pytest.mark.django_db
class TestFlightPlanningStatus:
    def test_flight_planning_status(self, client):
        resp = client.get(
            "/scd/flight_planning/status",
            **auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Ready"

    def test_u_space_flight_planning_status(self, client):
        resp = client.get(
            "/scd/flight_planning/u_space/status",
            **auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestFlightPlanningClearArea:
    def test_clear_area_missing_payload(self, client):
        resp = client.post(
            "/scd/flight_planning/clear_area_requests",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestFlightPlanUpsert:
    def test_delete_nonexistent_flight_plan(self, client):
        plan_id = str(uuid.uuid4())
        resp = client.delete(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            **auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] == "Failed"

    def test_upsert_flight_plan_invalid_data(self, client):
        plan_id = str(uuid.uuid4())
        resp = client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(SCD_PLAN_SCOPE),
        )
        # Missing required fields — view raises KeyError → 500
        assert resp.status_code == 500
