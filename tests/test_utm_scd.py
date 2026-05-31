"""
Integration tests: Strategic Conflict Detection (SCD) operations.

Covers:
- SCD v1 status and capabilities
- Flight planning (upsert / delete)
- Clear area request
- Flight planning sub-status endpoint
"""

import uuid

import pytest

BASE = "/scd"


# ── Status & Capabilities ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_scd_status(client):
    response = await client.get(f"{BASE}/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "operational"


@pytest.mark.anyio
async def test_scd_capabilities(client):
    response = await client.get(f"{BASE}/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert "capabilities" in body
    assert "BasicStrategicConflictDetection" in body["capabilities"]


# ── Flight Planning ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_upsert_flight_plan(client):
    """PUT a flight plan; response should contain the plan ID and result."""
    plan_id = str(uuid.uuid4())
    payload = {
        "intended_flight": {
            "volumes": [],
            "off_nominal_volumes": [],
            "priority": 0,
        },
        "usage_state": "Planned",
        "uas_state": "Nominal",
    }
    response = await client.put(f"{BASE}/flight_planning/flight_plans/{plan_id}", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["flight_plan_id"] == plan_id
    assert body["planning_result"] == "NotPlanned"


@pytest.mark.anyio
async def test_upsert_flight_plan_missing_body(client):
    """Missing required body fields should return 422."""
    plan_id = str(uuid.uuid4())
    response = await client.put(f"{BASE}/flight_planning/flight_plans/{plan_id}", json={})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_delete_flight_plan(client):
    """DELETE a flight plan should return 204."""
    plan_id = str(uuid.uuid4())
    response = await client.delete(f"{BASE}/flight_planning/flight_plans/{plan_id}")
    assert response.status_code == 204


# ── Clear Area ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_clear_area(client):
    payload = {
        "extent": {
            "volume": {
                "outline_circle": {"center": {"lng": -0.127, "lat": 51.507}, "radius": {"value": 300, "units": "M"}},
                "altitude_lower": {"value": 0, "reference": "W84", "units": "M"},
                "altitude_upper": {"value": 120, "reference": "W84", "units": "M"},
            },
            "time_start": {"value": "2025-01-01T00:00:00Z", "format": "RFC3339"},
            "time_end": {"value": "2025-01-01T01:00:00Z", "format": "RFC3339"},
        },
        "request_id": str(uuid.uuid4()),
    }
    response = await client.post(f"{BASE}/flight_planning/clear_area_requests", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "outcome" in body


# ── Sub-status ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_flight_planning_status(client):
    response = await client.get(f"{BASE}/flight_planning/status")
    assert response.status_code == 200
    assert response.json()["status"] == "operational"
