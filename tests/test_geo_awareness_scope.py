"""Tests that geo-awareness endpoints enforce the dedicated geo-awareness.test scope.

These exercise the real scope-checking path (``require_scope``) by replacing the
token-verification chokepoint (``verify_bearer_token``) with a stub that returns a
chosen set of scopes, rather than relying on the bypass flag (which grants all
scopes).
"""

from __future__ import annotations

import pytest

import flight_blender.auth.jwt_bearer as jwt_bearer

BASE = "/geo_fence_ops"


def _patch_scopes(monkeypatch, scopes):
    """Force ``verify_bearer_token`` to return the given scopes for any token."""
    monkeypatch.setattr(
        jwt_bearer,
        "verify_bearer_token",
        lambda token: {"scope": " ".join(scopes)},
    )


@pytest.mark.anyio
async def test_geo_awareness_status_scope_enforced(client, monkeypatch):
    _patch_scopes(monkeypatch, ["blender.read", "blender.write"])
    resp = await client.get(
        f"{BASE}/geo_awareness/status",
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_geo_awareness_status_allows_with_scope(client, monkeypatch):
    _patch_scopes(monkeypatch, ["geo-awareness.test"])
    resp = await client.get(
        f"{BASE}/geo_awareness/status",
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "Ready"


@pytest.mark.anyio
async def test_put_geozone_source_scope_enforced(client, monkeypatch):
    _patch_scopes(monkeypatch, ["blender.write"])
    resp = await client.put(
        f"{BASE}/geo_awareness/geospatial_data_sources/src1",
        json={"https_source": {"url": "https://example.com/z.json", "format": "ED-269"}},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_get_geozone_source_scope_enforced(client, monkeypatch):
    _patch_scopes(monkeypatch, ["blender.read"])
    resp = await client.get(
        f"{BASE}/geo_awareness/geospatial_data_sources/src1",
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_delete_geozone_source_scope_enforced(client, monkeypatch):
    _patch_scopes(monkeypatch, ["blender.write"])
    resp = await client.delete(
        f"{BASE}/geo_awareness/geospatial_data_sources/src1",
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_query_geozone_scope_enforced(client, monkeypatch):
    _patch_scopes(monkeypatch, ["blender.read"])
    resp = await client.post(
        f"{BASE}/geo_awareness/map/queries",
        json={"checks": []},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_query_geozone_allows_with_scope(client, monkeypatch):
    _patch_scopes(monkeypatch, ["geo-awareness.test"])
    resp = await client.post(
        f"{BASE}/geo_awareness/map/queries",
        json={"checks": []},
        headers={"Authorization": "Bearer faketoken"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "applicableGeozone" in body
