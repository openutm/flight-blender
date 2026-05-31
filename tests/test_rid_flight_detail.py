"""RID flight-detail create -> persist -> read parity tests (P3).

Django persists ``RIDFlightDetail`` rows (operator id/location, auth data,
UAS id, EU classification, operation description) and serves them back over the
peer-USS RID exchange (``get_uss_flight_details`` -> ``{"details": {...}}``).
The FastAPI port previously returned canned data and persisted nothing for the
detail-by-id path.

These tests pin a real round-trip: create a flight detail (persisted to the
SQLAlchemy ``RIDFlightDetail`` model), then read it back via the
Django-compatible peer-USS endpoint ``GET /uss/flights/<id>/details``.
"""

import uuid

import pytest

pytestmark = pytest.mark.anyio


async def test_create_flight_detail_persists_and_reads_back(client):
    payload = {
        "operation_description": "Aerial survey",
        "operator_id": "OP-123",
        "operator_location": {"position": {"lat": 51.5, "lng": -0.1}},
        "auth_data": {"format": 0, "data": ""},
        "uas_id": {"serial_number": "SN-1", "registration_id": "REG-1"},
        "eu_classification": {"category": "Open", "class": "C0"},
    }
    create = await client.post("/rid/flight_details", json=payload)
    assert create.status_code == 201
    body = create.json()
    detail_id = body["id"]
    assert body["operator_id"] == "OP-123"
    assert body["operation_description"] == "Aerial survey"

    # Read back via the Django-compatible peer-USS RID endpoint.
    read = await client.get(f"/uss/flights/{detail_id}/details")
    assert read.status_code == 200
    details = read.json()["details"]
    assert details["id"] == detail_id
    assert details["operator_id"] == "OP-123"
    # JSON fields round-trip back into structured objects (not raw strings).
    assert details["operator_location"] == {"position": {"lat": 51.5, "lng": -0.1}}
    assert details["uas_id"]["serial_number"] == "SN-1"


async def test_read_unknown_flight_detail_404(client):
    read = await client.get(f"/uss/flights/{uuid.uuid4()}/details")
    assert read.status_code == 404


async def test_create_flight_detail_minimal(client):
    """A near-empty detail still persists and reads back with at least its id."""
    create = await client.post("/rid/flight_details", json={"operator_id": "OP-MIN"})
    assert create.status_code == 201
    detail_id = create.json()["id"]
    read = await client.get(f"/uss/flights/{detail_id}/details")
    assert read.status_code == 200
    assert read.json()["details"]["operator_id"] == "OP-MIN"
