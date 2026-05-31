"""
Integration tests: Remote ID (RID) operations.

Covers:
- DSS subscription creation (Celery-mocked)
- ISA callback endpoint
- RID data retrieval by subscription
- Display data (view-box query)
- Flight details endpoint
- Notifications and capabilities
- USS qualifier test harness endpoints
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

BASE = "/rid"
FUTURE_END = (datetime.now(tz=timezone.utc) + timedelta(hours=4)).isoformat()


# ── DSS Subscription ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_dss_subscription(client):
    """Creating a subscription persists a record and queues a Celery task."""
    payload = {
        "view": "-1.0,51.0,1.0,52.0",
        "end_datetime": FUTURE_END,
    }
    response = await client.post(f"{BASE}/create_dss_subscription", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["view"] == "-1.0,51.0,1.0,52.0"
    assert "view_hash" in body


@pytest.mark.anyio
async def test_create_dss_subscription_missing_fields(client):
    """Missing required fields should produce 422."""
    response = await client.post(f"{BASE}/create_dss_subscription", json={"view": "0,0,1,1"})
    assert response.status_code == 422


# ── ISA Callback ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_dss_isa_callback(client):
    """DSS ISA callback returns a subscriptions payload."""
    isa_id = str(uuid.uuid4())
    response = await client.get(f"{BASE}/uss/identification_service_areas/{isa_id}")
    assert response.status_code == 200
    assert "subscriptions" in response.json()


# ── RID Data ──────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_rid_data_not_found(client):
    """Non-existent subscription should return 404."""
    response = await client.get(f"{BASE}/get_rid_data/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_rid_data_for_subscription(client):
    """After creating a subscription, its RID data should be retrievable."""
    payload = {"view": "0.0,51.0,1.0,52.0", "end_datetime": FUTURE_END}
    create_resp = await client.post(f"{BASE}/create_dss_subscription", json=payload)
    sub_id = create_resp.json()["id"]

    response = await client.get(f"{BASE}/get_rid_data/{sub_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["subscription_id"] == sub_id
    assert "flights" in body


# ── Display Data ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_rid_display_data(client):
    """Display data endpoint returns flights list (empty when no data)."""
    response = await client.get(f"{BASE}/display_data?view=-1.0,51.0,1.0,52.0")
    assert response.status_code == 200
    assert "flights" in response.json()
    assert isinstance(response.json()["flights"], list)


@pytest.mark.anyio
async def test_get_rid_display_data_missing_view(client):
    """Missing ``view`` query parameter should return 422."""
    response = await client.get(f"{BASE}/display_data")
    assert response.status_code == 422


@pytest.mark.anyio
async def test_get_rid_flight_detail(client):
    """Flight detail endpoint returns structured data for any flight ID."""
    flight_id = "TEST-RID-FLIGHT-001"
    response = await client.get(f"{BASE}/display_data/{flight_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == flight_id


# ── Capabilities ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_rid_capabilities(client):
    response = await client.get(f"{BASE}/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert "capabilities" in body
    assert "ASTM_F3411_22a" in body["capabilities"]


# ── User Notifications ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_rid_user_notifications_empty(client):
    response = await client.get(f"{BASE}/user_notifications")
    assert response.status_code == 200
    body = response.json()
    assert "notifications" in body
    assert isinstance(body["notifications"], list)


# ── USS Qualifier Test Harness ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_rid_test(client):
    test_id = str(uuid.uuid4())
    payload = {"requested_flights": [{"flight_id": "TEST-001"}]}
    response = await client.post(f"{BASE}/tests/{test_id}", json=payload)
    assert response.status_code == 201
    assert response.json()["version"] == 1


@pytest.mark.anyio
async def test_delete_rid_test(client):
    test_id = str(uuid.uuid4())
    response = await client.delete(f"{BASE}/tests/{test_id}/1")
    assert response.status_code == 204
