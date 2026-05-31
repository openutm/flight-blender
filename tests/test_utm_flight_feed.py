"""
Integration tests: Flight Feed / Air Traffic operations.

Covers:
- Signed telemetry public key CRUD
- Single and bulk air traffic observation ingestion (Celery-mocked)
- Get air traffic by session (Redis-mocked)
- Raw and signed telemetry submission
- 404 error handling for public keys
"""

import uuid
from datetime import datetime, timezone

import pytest

BASE = "/flight_stream"

KEY_PAYLOAD = {
    "key_id": "test-key-001",
    "url": "https://example.com/keys/test-key-001.pem",
    "is_active": True,
}

OBSERVATION_PAYLOAD = {
    "lat_dd": 51.5074,
    "lon_dd": -0.1278,
    "altitude_mm": 100000.0,
    "traffic_source": 1,
    "source_type": 0,
    "icao_address": "ABCDEF",
    "metadata": "{}",
}


# ── Public Keys ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_public_keys_empty(client):
    response = await client.get(f"{BASE}/public_keys/")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_create_public_key(client):
    response = await client.post(f"{BASE}/public_keys/", json=KEY_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["key_id"] == "test-key-001"
    assert body["is_active"] is True


@pytest.mark.anyio
async def test_get_public_key(client):
    create_resp = await client.post(f"{BASE}/public_keys/", json=KEY_PAYLOAD)
    key_id = create_resp.json()["id"]

    response = await client.get(f"{BASE}/public_keys/{key_id}")
    assert response.status_code == 200
    assert response.json()["id"] == key_id
    assert response.json()["key_id"] == "test-key-001"


@pytest.mark.anyio
async def test_get_public_key_not_found(client):
    response = await client.get(f"{BASE}/public_keys/{uuid.uuid4()}")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_update_public_key(client):
    create_resp = await client.post(f"{BASE}/public_keys/", json=KEY_PAYLOAD)
    key_id = create_resp.json()["id"]

    response = await client.put(
        f"{BASE}/public_keys/{key_id}",
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False


@pytest.mark.anyio
async def test_update_public_key_not_found(client):
    response = await client.put(
        f"{BASE}/public_keys/{uuid.uuid4()}",
        json={"is_active": False},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_public_key(client):
    create_resp = await client.post(f"{BASE}/public_keys/", json=KEY_PAYLOAD)
    key_id = create_resp.json()["id"]

    del_resp = await client.delete(f"{BASE}/public_keys/{key_id}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"{BASE}/public_keys/{key_id}")
    assert get_resp.status_code == 404


@pytest.mark.anyio
async def test_delete_public_key_not_found(client):
    response = await client.delete(f"{BASE}/public_keys/{uuid.uuid4()}")
    assert response.status_code == 404


# ── Air Traffic Observations ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_air_traffic_single(client):
    """Bulk-format payload with one observation should be queued via mocked Celery task."""
    session_id = str(uuid.uuid4())
    bulk_payload = {"observations": [OBSERVATION_PAYLOAD]}
    response = await client.post(f"{BASE}/set_air_traffic/{session_id}", json=bulk_payload)
    assert response.status_code == 200
    assert "queued" in response.json()["message"].lower()


@pytest.mark.anyio
async def test_bulk_set_air_traffic(client):
    """Multiple observations should be queued via mocked Celery bulk task."""
    session_id = str(uuid.uuid4())
    bulk_payload = {"observations": [{**OBSERVATION_PAYLOAD, "icao_address": f"OBS{i:03d}"} for i in range(5)]}
    response = await client.post(f"{BASE}/bulk_set_air_traffic/{session_id}", json=bulk_payload)
    assert response.status_code == 200
    body = response.json()
    assert "queued" in body["message"].lower()
    assert "5" in body["message"]


@pytest.mark.anyio
async def test_get_air_traffic_empty(client):
    """Get air traffic returns empty list when no observations exist (Redis mocked)."""
    session_id = str(uuid.uuid4())
    response = await client.get(f"{BASE}/get_air_traffic/{session_id}")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_set_air_traffic_validates_lat(client):
    """Latitude values outside [-90, 90] should be rejected."""
    session_id = str(uuid.uuid4())
    bad_obs = {**OBSERVATION_PAYLOAD, "lat_dd": 95.0}
    response = await client.post(f"{BASE}/set_air_traffic/{session_id}", json={"observations": [bad_obs]})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_set_air_traffic_validates_lon(client):
    """Longitude values outside [-180, 180] should be rejected."""
    session_id = str(uuid.uuid4())
    bad_obs = {**OBSERVATION_PAYLOAD, "lon_dd": 200.0}
    response = await client.post(f"{BASE}/set_air_traffic/{session_id}", json={"observations": [bad_obs]})
    assert response.status_code == 422


# ── Raw and signed telemetry ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_telemetry(client):
    """Raw telemetry observation is queued via Celery."""
    response = await client.post(f"{BASE}/set_telemetry", json=OBSERVATION_PAYLOAD)
    assert response.status_code == 200
    assert "queued" in response.json()["message"].lower()


@pytest.mark.anyio
async def test_set_signed_telemetry(client):
    """Signed ASTM RID telemetry is accepted."""
    payload = {
        "current_state": {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "position": {"lat": 51.5074, "lng": -0.1278, "alt": 100.0},
            "track": 90.0,
            "speed": 10.0,
        },
        "flight_details": {"id": "TEST-FLIGHT-001"},
    }
    response = await client.post(f"{BASE}/set_signed_telemetry", json=payload)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_set_air_traffic_multiple_observations(client):
    """set_air_traffic accepts a bulk payload with multiple observations."""
    session_id = str(uuid.uuid4())
    bulk_payload = {"observations": [{**OBSERVATION_PAYLOAD, "icao_address": f"MULTI{i:03d}"} for i in range(3)]}
    response = await client.post(f"{BASE}/set_air_traffic/{session_id}", json=bulk_payload)
    assert response.status_code == 200
    assert "queued" in response.json()["message"].lower()


@pytest.mark.anyio
async def test_set_air_traffic_metadata_as_dict(client):
    """set_air_traffic accepts metadata as a dict (toolkit sends dict, not str)."""
    session_id = str(uuid.uuid4())
    obs_with_dict_metadata = {**OBSERVATION_PAYLOAD, "metadata": {"key": "value", "count": 1}}
    response = await client.post(f"{BASE}/set_air_traffic/{session_id}", json={"observations": [obs_with_dict_metadata]})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_set_air_traffic_metadata_empty_dict(client):
    """set_air_traffic accepts empty dict metadata."""
    session_id = str(uuid.uuid4())
    obs_with_empty_metadata = {**OBSERVATION_PAYLOAD, "metadata": {}}
    response = await client.post(f"{BASE}/set_air_traffic/{session_id}", json={"observations": [obs_with_empty_metadata]})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_set_air_traffic_metadata_none(client):
    """set_air_traffic accepts null metadata (coerced to '{}')."""
    session_id = str(uuid.uuid4())
    obs_with_none_metadata = {**OBSERVATION_PAYLOAD, "metadata": None}
    response = await client.post(f"{BASE}/set_air_traffic/{session_id}", json={"observations": [obs_with_none_metadata]})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_set_telemetry_put_rid_payload(client):
    """PUT /set_telemetry accepts the RID telemetry payload from the verification toolkit and returns 201."""
    rid_payload = {
        "observations": [
            {
                "current_states": [
                    {
                        "timestamp": {"value": "2026-05-30T14:52:00.000Z", "format": "RFC3339"},
                        "timestamp_accuracy": 0.5,
                        "operational_status": "Airborne",
                        "position": {"lat": 46.9799, "lng": 7.4870, "alt": 120.0, "accuracy_h": "HAUnknown", "accuracy_v": "VAUnknown"},
                        "height": {"distance": 70.0, "reference": "TakeoffLocation"},
                        "track": 90.0,
                        "speed": 5.0,
                        "speed_accuracy": "SAUnknown",
                        "vertical_speed": 0.0,
                    }
                ],
                "flight_details": {
                    "id": "test-flight-001",
                    "operator_id": "OP456",
                    "operation_description": "Test",
                },
            }
        ]
    }
    response = await client.put(f"{BASE}/set_telemetry", json=rid_payload)
    assert response.status_code == 201
    body = response.json()
    assert "queued" in body["message"].lower()


@pytest.mark.anyio
async def test_set_telemetry_put_empty_observations(client):
    """PUT /set_telemetry with no current_states returns 201 with 0 queued."""
    response = await client.put(f"{BASE}/set_telemetry", json={"observations": [{"current_states": [], "flight_details": None}]})
    assert response.status_code == 201
    assert "0" in response.json()["message"]
