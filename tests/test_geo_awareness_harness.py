"""Integration tests for the InterUSS geo-awareness test harness endpoints.

Covers (bypass-auth mode, via the shared ``client`` fixture):
- ``GET /geo_awareness/status`` ED-269 shape
- ``POST /geo_awareness/map/queries`` present/absent spatial check
- ``PUT/GET/DELETE /geo_awareness/geospatial_data_sources/{id}`` lifecycle

The ``download_geozone_source`` task is patched by conftest (``.delay`` is a Mock),
so the PUT path records status without doing real I/O.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from flight_blender.models.geo_fence import GeoFence

BASE = "/geo_fence_ops"


async def _add_test_fence(db, *, bounds, geozone=None, is_test=True):
    now = datetime.now(timezone.utc)
    fence = GeoFence(
        raw_geo_fence="{}",
        geozone=geozone,
        upper_limit=120.0,
        lower_limit=0.0,
        altitude_ref=0,
        name="Harness Zone",
        bounds=bounds,
        status=1,
        is_test_dataset=is_test,
        start_datetime=now - timedelta(days=1),
        end_datetime=now + timedelta(days=365),
    )
    db.add(fence)
    await db.flush()
    return fence


# ── status ──────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_status_returns_ed269_shape(client):
    resp = await client.get(f"{BASE}/geo_awareness/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Ready"
    assert body["api_version"] == "latest"


# ── map/queries spatial check ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_map_query_present_when_point_in_zone(client, db):
    await _add_test_fence(db, bounds="-1.0,51.0,1.0,52.0")
    body = {"checks": [{"filter_sets": [{"position": {"longitude": 0.0, "latitude": 51.5}}]}]}
    resp = await client.post(f"{BASE}/geo_awareness/map/queries", json=body)
    assert resp.status_code == 200
    assert resp.json()["applicableGeozone"][0]["geozone"] == "Present"


@pytest.mark.anyio
async def test_map_query_absent_when_point_outside_zone(client, db):
    await _add_test_fence(db, bounds="-1.0,51.0,1.0,52.0")
    body = {"checks": [{"filter_sets": [{"position": {"longitude": 10.0, "latitude": 10.0}}]}]}
    resp = await client.post(f"{BASE}/geo_awareness/map/queries", json=body)
    assert resp.status_code == 200
    assert resp.json()["applicableGeozone"][0]["geozone"] == "Absent"


@pytest.mark.anyio
async def test_map_query_uses_stored_geometry_ring(client, db):
    # A triangular zone: point at (0.1, 0.1) is inside, (0.9, 0.9) is outside.
    feature = {
        "name": "Triangle",
        "geometry": [{"horizontalProjection": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]]}}],
    }
    await _add_test_fence(db, bounds="0.0,0.0,1.0,1.0", geozone=json.dumps(feature))
    inside = await client.post(
        f"{BASE}/geo_awareness/map/queries",
        json={"checks": [{"filter_sets": [{"position": {"longitude": 0.1, "latitude": 0.1}}]}]},
    )
    assert inside.json()["applicableGeozone"][0]["geozone"] == "Present"
    outside = await client.post(
        f"{BASE}/geo_awareness/map/queries",
        json={"checks": [{"filter_sets": [{"position": {"longitude": 0.9, "latitude": 0.9}}]}]},
    )
    # (0.9, 0.9) is inside the bbox but outside the triangle -> ray-cast says Absent.
    assert outside.json()["applicableGeozone"][0]["geozone"] == "Absent"


@pytest.mark.anyio
async def test_map_query_ignores_non_test_datasets(client, db):
    await _add_test_fence(db, bounds="-1.0,51.0,1.0,52.0", is_test=False)
    body = {"checks": [{"filter_sets": [{"position": {"longitude": 0.0, "latitude": 51.5}}]}]}
    resp = await client.post(f"{BASE}/geo_awareness/map/queries", json=body)
    assert resp.json()["applicableGeozone"][0]["geozone"] == "Absent"


@pytest.mark.anyio
async def test_map_query_empty_checks_is_absent(client):
    resp = await client.post(f"{BASE}/geo_awareness/map/queries", json={"checks": []})
    assert resp.status_code == 200
    assert resp.json()["applicableGeozone"][0]["geozone"] == "Absent"


@pytest.mark.anyio
async def test_map_query_accepts_interuss_body_shape(client):
    """The full InterUSS body shape (no ``volumes``) must not 422."""
    body = {"checks": [{"filter_sets": [{"position": {"uomDimensions": "M", "height": 50, "longitude": 0.0, "latitude": 51.5}}]}]}
    resp = await client.post(f"{BASE}/geo_awareness/map/queries", json=body)
    assert resp.status_code == 200


# ── geospatial data source lifecycle ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_put_source_records_status_and_enqueues(client):
    source_id = str(uuid.uuid4())
    from flight_blender.routers import geo_fence as gf_router

    payload = {"https_source": {"url": "https://example.com/zones.json", "format": "ED-269"}}
    resp = await client.put(f"{BASE}/geo_awareness/geospatial_data_sources/{source_id}", json=payload)
    assert resp.status_code == 200
    assert resp.json()["result"] == "Activating"
    # The patched download task was enqueued with the Django kwarg contract.
    gf_router.download_geozone_source.delay.assert_called_once()
    _, kwargs = gf_router.download_geozone_source.delay.call_args
    assert kwargs["geo_zone_url"] == "https://example.com/zones.json"
    assert kwargs["geozone_source_id"] == source_id


@pytest.mark.anyio
async def test_get_source_returns_stored_status(client):
    source_id = str(uuid.uuid4())
    payload = {"https_source": {"url": "https://example.com/zones.json", "format": "ED-269"}}
    await client.put(f"{BASE}/geo_awareness/geospatial_data_sources/{source_id}", json=payload)

    resp = await client.get(f"{BASE}/geo_awareness/geospatial_data_sources/{source_id}")
    assert resp.status_code == 200
    assert resp.json()["result"] == "Activating"


@pytest.mark.anyio
async def test_get_unknown_source_is_404(client):
    resp = await client.get(f"{BASE}/geo_awareness/geospatial_data_sources/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_delete_known_source_returns_deactivating(client):
    source_id = str(uuid.uuid4())
    payload = {"https_source": {"url": "https://example.com/zones.json", "format": "ED-269"}}
    await client.put(f"{BASE}/geo_awareness/geospatial_data_sources/{source_id}", json=payload)

    resp = await client.delete(f"{BASE}/geo_awareness/geospatial_data_sources/{source_id}")
    assert resp.status_code == 200
    assert resp.json()["result"] == "Deactivating"


@pytest.mark.anyio
async def test_delete_unknown_source_is_404(client):
    resp = await client.delete(f"{BASE}/geo_awareness/geospatial_data_sources/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── set_geozone validation gate ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_geozone_rejects_invalid_payload(client):
    resp = await client.post(f"{BASE}/set_geozone", json={"foo": "bar"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_set_geozone_accepts_valid_payload(client):
    valid = {
        "title": "T",
        "description": "D",
        "UASZoneList": [{"name": "Z", "geometry": [{"horizontalProjection": {"type": "Circle", "center": [0.0, 51.5], "radius": 100}}]}],
    }
    resp = await client.post(f"{BASE}/set_geozone", json=valid)
    assert resp.status_code == 200, resp.text
    assert "queued" in resp.json()["message"].lower()


# ── list/detail hide test datasets (Django parity) ─────────────────────────────────


@pytest.mark.anyio
async def test_list_excludes_test_datasets(client, db):
    await _add_test_fence(db, bounds="0,0,1,1", is_test=True)
    await _add_test_fence(db, bounds="0,0,1,1", is_test=False)
    resp = await client.get(f"{BASE}/geo_fence")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert all(not r["is_test_dataset"] for r in body["results"])


@pytest.mark.anyio
async def test_detail_hides_test_dataset(client, db):
    fence = await _add_test_fence(db, bounds="0,0,1,1", is_test=True)
    resp = await client.get(f"{BASE}/geo_fence/{fence.id}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_detail_returns_operational_fence(client, db):
    fence = await _add_test_fence(db, bounds="0,0,1,1", is_test=False)
    resp = await client.get(f"{BASE}/geo_fence/{fence.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(fence.id)
