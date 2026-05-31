"""Characterization tests for the constraint router GET endpoints.

The FastAPI ``routers/constraint.py`` exposes two read endpoints
(``GET /constraint_detail/{id}`` and ``GET /constraint_reference/{id}``) backed
by the ``ConstraintDetail`` / ``ConstraintReference`` SQLAlchemy models.

The Django ``constraint_operations`` app exposes **no** HTTP endpoints
(``urlpatterns == []`` and an empty ``views.py``); its only logic is the
DSS/USS *read* helpers (``constraints_helper.py`` /
``dss_constraints_helper.py``) which query constraints **from** the DSS and peer
USSes. There is no ``PUT /constraint_reference`` view, no ``GET /constraints``
view and no ``POST /query`` view to port. These tests therefore pin the
read-only port that actually exists and guard it against regression.
"""

import uuid

import pytest

from flight_blender.models.constraint import ConstraintDetail, ConstraintReference


@pytest.mark.anyio
async def test_get_constraint_detail_found_full_shape(client, db):
    """The detail endpoint returns id, volumes and the mapped ``type`` field."""
    detail = ConstraintDetail(volumes="[]", _type="EMERGENCY")
    db.add(detail)
    await db.flush()
    await db.commit()

    response = await client.get(f"/constraint_ops/constraint_detail/{detail.id}")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "id": str(detail.id),
        "volumes": "[]",
        "type": "EMERGENCY",
    }


@pytest.mark.anyio
async def test_get_constraint_detail_not_found(client):
    response = await client.get(f"/constraint_ops/constraint_detail/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Constraint detail not found"


@pytest.mark.anyio
async def test_get_constraint_reference_found_full_shape(client, db):
    """The reference endpoint returns the full serialized reference shape."""
    ref = ConstraintReference(
        uss_availability="Normal",
        ovn="constraint-ovn",
        manager="manager-1",
        uss_base_url="https://uss.example.com",
        version="7",
        is_live=True,
    )
    db.add(ref)
    await db.flush()
    await db.commit()

    response = await client.get(f"/constraint_ops/constraint_reference/{ref.id}")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "id": str(ref.id),
        "uss_availability": "Normal",
        "ovn": "constraint-ovn",
        "manager": "manager-1",
        "uss_base_url": "https://uss.example.com",
        "version": "7",
        "is_live": True,
    }


@pytest.mark.anyio
async def test_get_constraint_reference_not_found(client):
    response = await client.get(f"/constraint_ops/constraint_reference/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Constraint reference not found"


@pytest.mark.anyio
async def test_get_constraint_detail_invalid_uuid_returns_422(client):
    """A non-UUID path segment is rejected by the ``uuid.UUID`` path type."""
    response = await client.get("/constraint_ops/constraint_detail/not-a-uuid")
    assert response.status_code == 422
