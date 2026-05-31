"""
Integration tests: Conformance Monitoring operations.

Covers:
- Summary endpoint (empty and populated states)
- Status endpoint (conforming / non-conforming)
- Records list endpoint
"""

import pytest

BASE = "/conformance_monitoring_ops"


async def _seed_conformance_record(db, conformance_state: int = 1, resolved: bool = False):
    """Insert a ConformanceRecord directly into the test DB."""
    from flight_blender.models.conformance import ConformanceRecord

    record = ConformanceRecord(
        flight_declaration_id=None,
        conformance_state=conformance_state,
        description="Test conformance event",
        event_type="test_event",
        geofence_breach=False,
        resolved=resolved,
    )
    db.add(record)
    await db.flush()
    return record


# ── Summary ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_conformance_summary_empty(client):
    response = await client.get(f"{BASE}/conformance_record_summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_records"] == 0
    assert body["conforming_records"] == 0
    assert body["non_conforming_records"] == 0
    assert body["conformance_rate_percent"] == 100.0


@pytest.mark.anyio
async def test_conformance_summary_with_records(client, db):
    # Seed 2 conforming + 1 non-conforming
    await _seed_conformance_record(db, conformance_state=1)
    await _seed_conformance_record(db, conformance_state=1)
    await _seed_conformance_record(db, conformance_state=0)

    response = await client.get(f"{BASE}/conformance_record_summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_records"] == 3
    assert body["conforming_records"] == 2
    assert body["non_conforming_records"] == 1
    # Rate = 2/3 * 100 ≈ 66.67
    assert body["conformance_rate_percent"] == pytest.approx(66.67, rel=0.01)


# ── Status ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_conformance_status_conforming_when_empty(client):
    """No records → system is considered conforming."""
    response = await client.get(f"{BASE}/conformance_status")
    assert response.status_code == 200
    body = response.json()
    assert body["is_conforming"] is True
    assert body["active_nonconforming_count"] == 0


@pytest.mark.anyio
async def test_conformance_status_non_conforming(client, db):
    """An active non-conforming record → is_conforming=False."""
    await _seed_conformance_record(db, conformance_state=0, resolved=False)

    response = await client.get(f"{BASE}/conformance_status")
    assert response.status_code == 200
    body = response.json()
    assert body["is_conforming"] is False
    assert body["active_nonconforming_count"] >= 1


@pytest.mark.anyio
async def test_conformance_status_resolved_non_conforming(client, db):
    """A resolved non-conforming record should NOT affect is_conforming."""
    await _seed_conformance_record(db, conformance_state=0, resolved=True)

    response = await client.get(f"{BASE}/conformance_status")
    assert response.status_code == 200
    body = response.json()
    assert body["is_conforming"] is True


# ── Records list ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_conformance_records_empty(client):
    response = await client.get(f"{BASE}/get_conformance_records")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_conformance_records_returned(client, db):
    await _seed_conformance_record(db, conformance_state=1)
    await _seed_conformance_record(db, conformance_state=0)

    response = await client.get(f"{BASE}/get_conformance_records")
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 2
    # Most recent first
    event_types = {r["event_type"] for r in records}
    assert "test_event" in event_types
