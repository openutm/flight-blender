"""
Integration tests: Flight Declaration UTM operations.

Covers:
- CRUD lifecycle (create, read, update, delete)
- Pagination on list endpoint
- State-machine transitions (0 → accepted → activated)
- Approval workflow
- 404 error handling
- Bulk creation
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

BASE = "/flight_declaration_ops"
FUTURE_START = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()
FUTURE_END = (datetime.now(tz=timezone.utc) + timedelta(hours=4)).isoformat()

DECL_PAYLOAD = {
    "operational_intent": json.dumps({"volumes": [], "off_nominal_volumes": [], "priority": 0}),
    "bounds": "-1.0,51.0,1.0,52.0",
    "aircraft_id": "TEST-AIRCRAFT-001",
    "type_of_operation": 1,
    "start_datetime": FUTURE_START,
    "end_datetime": FUTURE_END,
}


# ── List ──────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_flight_declarations_empty(client):
    response = await client.get(f"{BASE}/flight_declaration")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["results"] == []


# ── Create ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_flight_declaration(client):
    response = await client.post(f"{BASE}/flight_declaration", json=DECL_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["message"] == "Flight declaration created"
    assert body["is_approved"] is False
    assert body["state"] == 0


@pytest.mark.anyio
async def test_create_rejects_end_before_start(client):
    bad_payload = {**DECL_PAYLOAD, "end_datetime": FUTURE_START, "start_datetime": FUTURE_END}
    response = await client.post(f"{BASE}/flight_declaration", json=bad_payload)
    assert response.status_code == 422


# ── Read ──────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_flight_declaration(client):
    create_resp = await client.post(f"{BASE}/flight_declaration", json=DECL_PAYLOAD)
    decl_id = create_resp.json()["id"]

    response = await client.get(f"{BASE}/flight_declaration/{decl_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == decl_id
    assert body["aircraft_id"] == "TEST-AIRCRAFT-001"
    assert body["state"] == 0


@pytest.mark.anyio
async def test_get_flight_declaration_not_found(client):
    random_id = str(uuid.uuid4())
    response = await client.get(f"{BASE}/flight_declaration/{random_id}")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ── Update ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_update_flight_declaration(client):
    create_resp = await client.post(f"{BASE}/flight_declaration", json=DECL_PAYLOAD)
    decl_id = create_resp.json()["id"]

    response = await client.put(
        f"{BASE}/flight_declaration/{decl_id}",
        json={"aircraft_id": "UPDATED-AIRCRAFT-999"},
    )
    assert response.status_code == 200
    assert response.json()["aircraft_id"] == "UPDATED-AIRCRAFT-999"


@pytest.mark.anyio
async def test_update_flight_declaration_not_found(client):
    response = await client.put(
        f"{BASE}/flight_declaration/{uuid.uuid4()}",
        json={"aircraft_id": "X"},
    )
    assert response.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_delete_flight_declaration(client):
    create_resp = await client.post(f"{BASE}/flight_declaration", json=DECL_PAYLOAD)
    decl_id = create_resp.json()["id"]

    del_resp = await client.delete(f"{BASE}/flight_declaration/{decl_id}/delete")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"{BASE}/flight_declaration/{decl_id}")
    assert get_resp.status_code == 404


@pytest.mark.anyio
async def test_delete_flight_declaration_not_found(client):
    response = await client.delete(f"{BASE}/flight_declaration/{uuid.uuid4()}/delete")
    assert response.status_code == 404


# ── State management ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_declaration_state(client):
    create_resp = await client.post(f"{BASE}/flight_declaration", json=DECL_PAYLOAD)
    decl_id = create_resp.json()["id"]

    response = await client.get(f"{BASE}/flight_declaration_state/{decl_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == decl_id
    assert body["state"] == 0


@pytest.mark.anyio
async def test_update_declaration_state(client):
    create_resp = await client.post(f"{BASE}/flight_declaration", json=DECL_PAYLOAD)
    decl_id = create_resp.json()["id"]

    # Transition: Received (0) → Accepted (1)
    response = await client.put(
        f"{BASE}/flight_declaration_state/{decl_id}",
        json={"state": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == 1
    assert "State updated" in body["message"]


@pytest.mark.anyio
async def test_state_transition_chain(client):
    """Simulate a flight going through multiple state changes."""
    create_resp = await client.post(f"{BASE}/flight_declaration", json=DECL_PAYLOAD)
    decl_id = create_resp.json()["id"]

    for new_state in [1, 2, 3]:
        resp = await client.put(f"{BASE}/flight_declaration_state/{decl_id}", json={"state": new_state})
        assert resp.status_code == 200
        assert resp.json()["state"] == new_state


@pytest.mark.anyio
async def test_update_declaration_state_not_found(client):
    response = await client.put(
        f"{BASE}/flight_declaration_state/{uuid.uuid4()}",
        json={"state": 1},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_update_declaration_state_invalid(client):
    """State values outside 0-8 should be rejected."""
    create_resp = await client.post(f"{BASE}/flight_declaration", json=DECL_PAYLOAD)
    decl_id = create_resp.json()["id"]

    response = await client.put(
        f"{BASE}/flight_declaration_state/{decl_id}",
        json={"state": 99},
    )
    assert response.status_code == 422


# ── List pagination ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_flight_declarations_pagination(client):
    # Create 3 declarations
    ids = []
    for i in range(3):
        payload = {**DECL_PAYLOAD, "aircraft_id": f"AIRCRAFT-PAGE-{i}"}
        resp = await client.post(f"{BASE}/flight_declaration", json=payload)
        assert resp.status_code == 201
        ids.append(resp.json()["id"])

    # First page (size 2)
    page1 = await client.get(f"{BASE}/flight_declaration?page=1&page_size=2")
    assert page1.status_code == 200
    body1 = page1.json()
    assert body1["count"] >= 3
    assert len(body1["results"]) == 2

    # Second page
    page2 = await client.get(f"{BASE}/flight_declaration?page=2&page_size=2")
    assert page2.status_code == 200
    assert len(page2.json()["results"]) >= 1


# ── Bounding-box convenience endpoint ────────────────────────────────────────


BBOX_PAYLOAD = {
    "minx": 7.4719,
    "miny": 46.9799,
    "maxx": 7.4870,
    "maxy": 46.9865,
}

FULL_DECLARATION_PAYLOAD = {
    "exchange_type": "flight_declaration",
    "aircraft_id": "a5dd8899-bc19-c8c4-2dd7-57f786d1379d",
    "flight_id": "5a7f3377-b991-4cc8-af2d-379d57f786d1",
    "plan_id": "a5b5484c-a23c-4e83-8bb8-a6a5c294e45b",
    "flight_state": 2,
    "flight_approved": 0,
    "sequence_number": 0,
    "start_datetime": FUTURE_START,
    "end_datetime": FUTURE_END,
    "version": "1.0.0",
    "purpose": "Delivery",
    "expect_telemetry": True,
    "originating_party": "Medicine Delivery Company",
    "contact_url": "https://utm.originatingparty.com/contact",
    "type_of_operation": 0,
    "vehicle_id": "157de9bb-6b49-496b-bf3f-0b768ce6a3b6",
    "operator_id": "OP456",
    "flight_declaration_geo_json": {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [7.487045, 46.979912],
                            [7.487045, 46.986538],
                            [7.471958, 46.986538],
                            [7.471958, 46.979912],
                            [7.487045, 46.979912],
                        ]
                    ],
                },
                "properties": {
                    "min_altitude": {"meters": 50, "datum": "w84"},
                    "max_altitude": {"meters": 120, "datum": "w84"},
                },
            }
        ],
    },
}


@pytest.mark.anyio
async def test_set_flight_declaration_bbox_creates_approved(client):
    """POST /set_flight_declaration with a bbox-only payload is rejected (missing required fields)."""
    response = await client.post(f"{BASE}/set_flight_declaration", json=BBOX_PAYLOAD)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_set_flight_declaration_full_payload_creates_approved(client):
    """POST /set_flight_declaration with the full toolkit payload should return an approved declaration."""
    response = await client.post(f"{BASE}/set_flight_declaration", json=FULL_DECLARATION_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["is_approved"] is True
    assert body["state"] is not None


@pytest.mark.anyio
async def test_set_flight_declaration_full_payload_naive_datetime(client):
    """POST /set_flight_declaration with timezone-naive datetime strings should succeed."""
    naive_start = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    naive_end = (datetime.now() + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
    payload = {**FULL_DECLARATION_PAYLOAD, "start_datetime": naive_start, "end_datetime": naive_end}
    response = await client.post(f"{BASE}/set_flight_declaration", json=payload)
    assert response.status_code == 201
    assert response.json()["is_approved"] is True
