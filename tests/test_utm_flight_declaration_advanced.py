"""
Additional integration tests: Flight Declaration advanced operations.

Covers uncovered endpoints:
- Network flight declarations
- set_flight_declaration (full request format)
- set_flight_declarations_bulk
- set_operational_intent
- set_operational_intents_bulk
- get_network_declarations_by_view
- approval workflow get/set
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

BASE = "/flight_declaration_ops"
FUTURE_START = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()
FUTURE_END = (datetime.now(tz=timezone.utc) + timedelta(hours=4)).isoformat()

FULL_REQUEST_PAYLOAD = {
    "aircraft_id": "AIRCRAFT-FULL-001",
    "type_of_operation": 1,
    "flight_state": 1,
    "originating_party": "Test Party",
    "start_datetime": FUTURE_START,
    "end_datetime": FUTURE_END,
    "flight_declaration_geo_json": {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-0.1, 51.4], [0.1, 51.4], [0.1, 51.6], [-0.1, 51.6], [-0.1, 51.4]]],
                },
                "properties": {"min_altitude": {"meters": 50, "datum": "W84"}, "max_altitude": {"meters": 120, "datum": "W84"}},
            }
        ],
    },
}


# ── set_flight_declaration (full request) ─────────────────────────────────────


@pytest.mark.anyio
async def test_set_flight_declaration(client):
    response = await client.post(f"{BASE}/set_flight_declaration", json=FULL_REQUEST_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["is_approved"] is True


@pytest.mark.anyio
async def test_set_flight_declaration_minimal_payload(client):
    """Payload missing required fields (start/end datetime) is rejected."""
    payload = {"aircraft_id": "MIN-AIRCRAFT", "type_of_operation": 0}
    response = await client.post(f"{BASE}/set_flight_declaration", json=payload)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_set_flight_declaration_no_geojson(client):
    payload = {
        "aircraft_id": "NO-GEO-AIRCRAFT",
        "type_of_operation": 0,
        "start_datetime": FUTURE_START,
        "end_datetime": FUTURE_END,
    }
    response = await client.post(f"{BASE}/set_flight_declaration", json=payload)
    assert response.status_code == 201


# ── Bulk creation ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_bulk_create_flight_declarations(client):
    payloads = [FULL_REQUEST_PAYLOAD, {**FULL_REQUEST_PAYLOAD, "aircraft_id": "BULK-002"}]
    response = await client.post(f"{BASE}/set_flight_declarations_bulk", json=payloads)
    assert response.status_code == 200
    body = response.json()
    assert body["submitted"] == 2
    assert body["failed"] == 0
    assert len(body["results"]) == 2
    for result in body["results"]:
        assert result["success"] is True


@pytest.mark.anyio
async def test_bulk_create_empty_list(client):
    response = await client.post(f"{BASE}/set_flight_declarations_bulk", json=[])
    assert response.status_code == 200
    body = response.json()
    assert body["submitted"] == 0
    assert body["failed"] == 0


# ── Operational intent ingest ─────────────────────────────────────────────────

OP_INTENT_PAYLOAD = {
    "aircraft_id": "OP-INTENT-001",
    "type_of_operation": 1,
    "start_datetime": FUTURE_START,
    "end_datetime": FUTURE_END,
    "operational_intent_volume4ds": [
        {
            "volume": {
                "outline_polygon": {
                    "vertices": [
                        {"lat": 51.4, "lng": -0.1},
                        {"lat": 51.6, "lng": -0.1},
                        {"lat": 51.6, "lng": 0.1},
                        {"lat": 51.4, "lng": 0.1},
                    ]
                },
                "altitude_lower": {"value": 0, "units": "M"},
                "altitude_upper": {"value": 120, "units": "M"},
            }
        }
    ],
}


@pytest.mark.anyio
async def test_set_operational_intent(client):
    response = await client.post(f"{BASE}/set_operational_intent", json=OP_INTENT_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["is_approved"] is True


@pytest.mark.anyio
async def test_set_operational_intent_geojson_coords(client):
    payload = {
        "aircraft_id": "OP-GEOJSON-001",
        "start_datetime": FUTURE_START,
        "end_datetime": FUTURE_END,
        "operational_intent_volume4ds": [
            {
                "volume": {
                    "outline_polygon": {
                        "type": "Polygon",
                        "coordinates": [[[-0.1, 51.4], [0.1, 51.4], [0.1, 51.6], [-0.1, 51.6], [-0.1, 51.4]]],
                    },
                }
            }
        ],
    }
    response = await client.post(f"{BASE}/set_operational_intent", json=payload)
    assert response.status_code == 201


@pytest.mark.anyio
async def test_set_operational_intent_empty_volumes(client):
    """Empty volumes list and missing required datetime fields are rejected."""
    payload = {"aircraft_id": "OP-EMPTY-001", "operational_intent_volume4ds": []}
    response = await client.post(f"{BASE}/set_operational_intent", json=payload)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_set_operational_intents_bulk(client):
    payloads = [OP_INTENT_PAYLOAD, {**OP_INTENT_PAYLOAD, "aircraft_id": "OP-BULK-002"}]
    response = await client.post(f"{BASE}/set_operational_intents_bulk", json=payloads)
    assert response.status_code == 200
    body = response.json()
    assert body["submitted"] == 2
    assert body["failed"] == 0


# ── Network declarations ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_network_flight_declarations(client):
    create_resp = await client.post(
        f"{BASE}/flight_declaration",
        json={
            "operational_intent": "{}",
            "bounds": "-1,51,1,52",
            "aircraft_id": "NETWORK-001",
            "type_of_operation": 1,
            "start_datetime": FUTURE_START,
            "end_datetime": FUTURE_END,
        },
    )
    decl_id = create_resp.json()["id"]

    response = await client.get(f"{BASE}/flight_declaration/{decl_id}/network_flight_declarations")
    assert response.status_code == 200
    body = response.json()
    assert body["declaration_id"] == decl_id
    assert body["network_declarations"] == []


@pytest.mark.anyio
async def test_get_network_flight_declarations_not_found(client):
    response = await client.get(f"{BASE}/flight_declaration/{uuid.uuid4()}/network_flight_declarations")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_network_declarations_by_view(client):
    response = await client.get(
        f"{BASE}/network_flight_declarations_by_view",
        params={"view": "51.0,-1.0,52.0,1.0"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "view" in body
    assert body["network_declarations"] == []


# ── Approval workflow ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_declaration_review(client):
    create_resp = await client.post(
        f"{BASE}/flight_declaration",
        json={
            "operational_intent": "{}",
            "bounds": "-1,51,1,52",
            "aircraft_id": "REVIEW-001",
            "type_of_operation": 1,
            "start_datetime": FUTURE_START,
            "end_datetime": FUTURE_END,
        },
    )
    decl_id = create_resp.json()["id"]

    response = await client.get(f"{BASE}/flight_declaration_review/{decl_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == decl_id
    assert "is_approved" in body


@pytest.mark.anyio
async def test_set_declaration_approval(client):
    create_resp = await client.post(
        f"{BASE}/flight_declaration",
        json={
            "operational_intent": "{}",
            "bounds": "-1,51,1,52",
            "aircraft_id": "APPROVAL-001",
            "type_of_operation": 1,
            "start_datetime": FUTURE_START,
            "end_datetime": FUTURE_END,
        },
    )
    decl_id = create_resp.json()["id"]

    response = await client.post(
        f"{BASE}/flight_declaration_review/{decl_id}",
        json={"is_approved": True, "approved_by": "test-reviewer"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_approved"] is True


# ── DSS submission ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_submit_to_dss(client):
    create_resp = await client.post(
        f"{BASE}/flight_declaration",
        json={
            "operational_intent": "{}",
            "bounds": "-1,51,1,52",
            "aircraft_id": "DSS-001",
            "type_of_operation": 1,
            "start_datetime": FUTURE_START,
            "end_datetime": FUTURE_END,
        },
    )
    decl_id = create_resp.json()["id"]

    response = await client.post(f"{BASE}/flight_declaration/{decl_id}/submit_to_dss")
    assert response.status_code == 200
    assert "queued" in response.json()["message"].lower()


@pytest.mark.anyio
async def test_submit_to_dss_not_found(client):
    response = await client.post(f"{BASE}/flight_declaration/{uuid.uuid4()}/submit_to_dss")
    assert response.status_code == 404


# ── Bulk endpoints run strategic deconfliction (regression guard) ──────────────


@pytest.mark.anyio
async def test_bulk_create_flight_declarations_runs_deconfliction(client, monkeypatch):
    """Bulk flight-declaration create must run deconfliction, not optimistically approve."""
    from flight_blender.common.enums import OperationState
    from flight_blender.routers import flight_declaration as fd_router

    async def fake_run_deconfliction(*args, **kwargs):
        return False, int(OperationState.REJECTED)

    monkeypatch.setattr(fd_router, "_run_deconfliction", fake_run_deconfliction)

    response = await client.post(f"{BASE}/set_flight_declarations_bulk", json=[FULL_REQUEST_PAYLOAD])
    assert response.status_code == 200
    created_id = response.json()["results"][0]["id"]

    detail = await client.get(f"{BASE}/flight_declaration/{created_id}")
    assert detail.status_code == 200
    assert detail.json()["is_approved"] is False
    assert detail.json()["state"] == int(OperationState.REJECTED)


@pytest.mark.anyio
async def test_bulk_set_operational_intents_runs_deconfliction(client, monkeypatch):
    """Bulk op-intent create must run deconfliction, not optimistically approve."""
    from flight_blender.common.enums import OperationState
    from flight_blender.routers import flight_declaration as fd_router

    async def fake_run_deconfliction(*args, **kwargs):
        return False, int(OperationState.REJECTED)

    monkeypatch.setattr(fd_router, "_run_deconfliction", fake_run_deconfliction)

    response = await client.post(f"{BASE}/set_operational_intents_bulk", json=[OP_INTENT_PAYLOAD])
    assert response.status_code == 200
    created_id = response.json()["results"][0]["id"]

    detail = await client.get(f"{BASE}/flight_declaration/{created_id}")
    assert detail.status_code == 200
    assert detail.json()["is_approved"] is False
    assert detail.json()["state"] == int(OperationState.REJECTED)
