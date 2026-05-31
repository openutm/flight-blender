"""
Integration tests: Geo Fence UTM operations.

Covers:
- CRUD lifecycle (create, read, update, delete)
- Convenience ``set_geo_fence`` endpoint
- GeoZone queue endpoint (Celery-mocked)
- Pagination on list endpoint
- 404 error handling
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

BASE = "/geo_fence_ops"
FUTURE_START = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()
FUTURE_END = (datetime.now(tz=timezone.utc) + timedelta(hours=4)).isoformat()

FENCE_PAYLOAD = {
    "name": "Test Fence Alpha",
    "upper_limit": 150.0,
    "lower_limit": 0.0,
    "altitude_ref": 0,
    "bounds": "-1.0,51.0,1.0,52.0",
    "start_datetime": FUTURE_START,
    "end_datetime": FUTURE_END,
}


# ── List ──────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_geo_fences_empty(client):
    response = await client.get(f"{BASE}/geo_fence")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["results"] == []


# ── Create ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_geo_fence(client):
    response = await client.post(f"{BASE}/geo_fence", json=FENCE_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["name"] == "Test Fence Alpha"
    assert body["upper_limit"] == 150.0
    assert body["lower_limit"] == 0.0


@pytest.mark.anyio
async def test_create_geo_fence_missing_required_fields(client):
    response = await client.post(f"{BASE}/geo_fence", json={"name": "incomplete"})
    assert response.status_code == 422


# ── Read ──────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_geo_fence(client):
    create_resp = await client.post(f"{BASE}/geo_fence", json=FENCE_PAYLOAD)
    fence_id = create_resp.json()["id"]

    response = await client.get(f"{BASE}/geo_fence/{fence_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == fence_id
    assert body["name"] == "Test Fence Alpha"


@pytest.mark.anyio
async def test_get_geo_fence_not_found(client):
    response = await client.get(f"{BASE}/geo_fence/{uuid.uuid4()}")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ── Update ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_update_geo_fence(client):
    create_resp = await client.post(f"{BASE}/geo_fence", json=FENCE_PAYLOAD)
    fence_id = create_resp.json()["id"]

    response = await client.put(
        f"{BASE}/geo_fence/{fence_id}",
        json={"upper_limit": 300.0, "name": "Updated Fence"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["upper_limit"] == 300.0
    assert body["name"] == "Updated Fence"


@pytest.mark.anyio
async def test_update_geo_fence_not_found(client):
    response = await client.put(
        f"{BASE}/geo_fence/{uuid.uuid4()}",
        json={"upper_limit": 100.0},
    )
    assert response.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_delete_geo_fence(client):
    create_resp = await client.post(f"{BASE}/geo_fence", json=FENCE_PAYLOAD)
    fence_id = create_resp.json()["id"]

    del_resp = await client.delete(f"{BASE}/geo_fence/{fence_id}/delete")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"{BASE}/geo_fence/{fence_id}")
    assert get_resp.status_code == 404


@pytest.mark.anyio
async def test_delete_geo_fence_not_found(client):
    response = await client.delete(f"{BASE}/geo_fence/{uuid.uuid4()}/delete")
    assert response.status_code == 404


# ── Convenience endpoints ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_geo_fence(client):
    """``set_geo_fence`` is a convenience alias that creates a fence."""
    response = await client.post(f"{BASE}/set_geo_fence", json=FENCE_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert body["name"] == "Test Fence Alpha"


@pytest.mark.anyio
async def test_set_geozone_queues_task(client):
    """GeoZone ingestion queues a Celery task; response should confirm queuing."""
    geozone_payload = {
        "UASZoneList": [
            {
                "geometry": [{"horizontalProjection": {"type": "Circle", "center": [0.0, 51.5], "radius": 500}}],
                "applicability": {
                    "startDateTime": FUTURE_START,
                    "endDateTime": FUTURE_END,
                    "schedule": [],
                },
                "zoneAuthority": [{"name": "CAA"}],
            }
        ]
    }
    response = await client.post(f"{BASE}/set_geozone", json=geozone_payload)
    assert response.status_code == 200
    assert "queued" in response.json()["message"].lower()


# ── Pagination ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_geo_fences_pagination(client):
    for i in range(3):
        payload = {**FENCE_PAYLOAD, "name": f"Fence Paginate {i}"}
        resp = await client.post(f"{BASE}/geo_fence", json=payload)
        assert resp.status_code == 201

    page1 = await client.get(f"{BASE}/geo_fence?page=1&page_size=2")
    assert page1.status_code == 200
    assert page1.json()["count"] >= 3
    assert len(page1.json()["results"]) == 2


# ── GeoJSON PUT endpoint ──────────────────────────────────────────────────────


GEOJSON_FENCE_PAYLOAD = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "upper_limit": 500,
                "lower_limit": 100,
                "start_time": "2023-03-07T16:48:41",
                "end_time": "2027-03-07T16:48:41",
                "name": "Geofence 1",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [30.142621994018555, -1.985209815625593],
                        [30.156269073486328, -1.985209815625593],
                        [30.156269073486328, -1.9534712184928378],
                        [30.142621994018555, -1.9534712184928378],
                        [30.142621994018555, -1.985209815625593],
                    ]
                ],
            },
        }
    ],
}


@pytest.mark.anyio
async def test_set_geo_fence_put_geojson(client):
    """PUT /set_geo_fence with a GeoJSON FeatureCollection should create a fence."""
    response = await client.put(f"{BASE}/set_geo_fence", json=GEOJSON_FENCE_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert body["name"] == "Geofence 1"
    assert body["upper_limit"] == 500.0
    assert body["lower_limit"] == 100.0


@pytest.mark.anyio
async def test_set_geo_fence_put_no_features(client):
    """PUT /set_geo_fence with an empty features array should return 422."""
    response = await client.put(f"{BASE}/set_geo_fence", json={"type": "FeatureCollection", "features": []})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_set_geo_fence_put_naive_datetime_strings(client):
    """PUT /set_geo_fence with timezone-naive ISO datetime strings should succeed (UTC applied)."""
    naive_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[7.47, 46.97], [7.48, 46.97], [7.48, 46.98], [7.47, 46.98], [7.47, 46.97]]],
                },
                "properties": {
                    "name": "Naive TZ Fence",
                    "upper_limit": 500,
                    "lower_limit": 0,
                    "start_time": "2023-03-07T16:48:41",
                    "end_time": "2023-03-07T18:48:41",
                },
            }
        ],
    }
    response = await client.put(f"{BASE}/set_geo_fence", json=naive_geojson)
    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert body["name"] == "Naive TZ Fence"
