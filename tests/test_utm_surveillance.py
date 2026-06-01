"""
Integration tests: Surveillance monitoring operations.

Covers:
- Surveillance health endpoint
- Heartbeat session start/stop lifecycle
- List surveillance sensors
- Service metrics with/without date filters
- Sensor health update
- Sensor health notifications list
"""

import uuid

import pytest

BASE = "/surveillance_monitoring_ops"


# ── Health ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_surveillance_health_no_sensors(client):
    response = await client.get(f"{BASE}/health/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "outage"
    assert body["active_sessions"] == 0


# ── Heartbeat session ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_start_heartbeat_session(client):
    session_id = str(uuid.uuid4())
    response = await client.put(
        f"{BASE}/start_stop_surveillance_heartbeat_track/{session_id}",
        json={"action": "start"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert "started" in body["message"].lower()


@pytest.mark.anyio
async def test_start_duplicate_heartbeat_session_rejected(client):
    session_id = str(uuid.uuid4())
    await client.put(
        f"{BASE}/start_stop_surveillance_heartbeat_track/{session_id}",
        json={"action": "start"},
    )
    # Second start with same ID must fail
    response = await client.put(
        f"{BASE}/start_stop_surveillance_heartbeat_track/{session_id}",
        json={"action": "start"},
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_stop_heartbeat_session(client):
    session_id = str(uuid.uuid4())
    await client.put(
        f"{BASE}/start_stop_surveillance_heartbeat_track/{session_id}",
        json={"action": "start"},
    )
    response = await client.put(
        f"{BASE}/start_stop_surveillance_heartbeat_track/{session_id}",
        json={"action": "stop"},
    )
    assert response.status_code == 200
    assert "stopped" in response.json()["message"].lower()


@pytest.mark.anyio
async def test_stop_nonexistent_session_returns_404(client):
    session_id = str(uuid.uuid4())
    response = await client.put(
        f"{BASE}/start_stop_surveillance_heartbeat_track/{session_id}",
        json={"action": "stop"},
    )
    assert response.status_code == 404


# ── Sensors list ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_surveillance_sensors_empty(client):
    response = await client.get(f"{BASE}/list_surveillance_sensors")
    assert response.status_code == 200
    assert response.json() == []


# ── Service metrics ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_service_metrics_no_data(client):
    response = await client.get(f"{BASE}/service_metrics")
    assert response.status_code == 200
    body = response.json()
    assert "heartbeat_rates" in body
    assert "track_update_probabilities" in body
    assert "active_sessions" in body


@pytest.mark.anyio
async def test_service_metrics_with_date_filters(client):
    response = await client.get(
        f"{BASE}/service_metrics",
        params={"start_date": "2024-01-01T00:00:00", "end_date": "2024-12-31T23:59:59"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["window_start"] == "2024-01-01T00:00:00+00:00"
    assert body["window_end"] == "2024-12-31T23:59:59+00:00"


@pytest.mark.anyio
async def test_service_metrics_with_invalid_dates(client):
    # Invalid dates should be ignored gracefully (no 500 error)
    response = await client.get(
        f"{BASE}/service_metrics",
        params={"start_date": "not-a-date", "end_date": "also-not-a-date"},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_service_metrics_with_session_id(client):
    session_id = str(uuid.uuid4())
    response = await client.get(f"{BASE}/service_metrics", params={"session_id": session_id})
    assert response.status_code == 200
    body = response.json()
    assert body["heartbeat_rates"][0]["session_id"] == session_id


# ── Sensor health notifications ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_sensor_health_notifications_empty(client):
    response = await client.get(f"{BASE}/list_sensor_health_notifications")
    assert response.status_code == 200
    assert response.json() == []
