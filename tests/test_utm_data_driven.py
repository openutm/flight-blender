"""
Data-driven integration tests for surveillance, conformance, and constraint endpoints.

These tests pre-populate the database via the `db` fixture and then call API endpoints
to exercise paths that are only reached when data exists.
"""

import uuid

import pytest

from flight_blender.models.conformance import ConformanceRecord
from flight_blender.models.constraint import ConstraintDetail, ConstraintReference
from flight_blender.models.surveillance import (
    SurveillanceSensor,
    SurveillanceSensorHealth,
)


# ══════════════════════════════════════════════════════════════════════════════
# Surveillance with data
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_surveillance_health_operational(client, db):
    """Health should return 'operational' when a sensor has status 'operational'."""
    sensor = SurveillanceSensor(sensor_identifier="SENSOR-001")
    db.add(sensor)
    await db.flush()

    health = SurveillanceSensorHealth(sensor_id=sensor.id, status="operational")
    db.add(health)
    await db.flush()
    await db.commit()

    response = await client.get("/surveillance_monitoring_ops/health/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "operational"
    assert body["active_sessions"] >= 1


@pytest.mark.anyio
async def test_surveillance_health_degraded(client, db):
    """Health should return 'degraded' when a sensor has status 'degraded'."""
    sensor = SurveillanceSensor(sensor_identifier="SENSOR-DEGRADED")
    db.add(sensor)
    await db.flush()

    health = SurveillanceSensorHealth(sensor_id=sensor.id, status="degraded")
    db.add(health)
    await db.flush()
    await db.commit()

    response = await client.get("/surveillance_monitoring_ops/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


@pytest.mark.anyio
async def test_list_surveillance_sensors_with_data(client, db):
    """List endpoint should return created sensors."""
    sensor = SurveillanceSensor(sensor_identifier="SENSOR-LIST-001")
    db.add(sensor)
    await db.flush()
    await db.commit()

    response = await client.get("/surveillance_monitoring_ops/list_surveillance_sensors")
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 1
    identifiers = [s["sensor_identifier"] for s in body]
    assert "SENSOR-LIST-001" in identifiers


@pytest.mark.anyio
async def test_update_sensor_health_not_found(client):
    """Sensor update should return 404 for unknown sensor."""
    response = await client.put(
        f"/surveillance_monitoring_ops/update_sensor_health/{uuid.uuid4()}",
        json={"status": "operational", "recovery_type": None},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_update_sensor_health_creates_record(client, db):
    """Updating sensor health for an existing sensor should succeed."""
    sensor = SurveillanceSensor(sensor_identifier="SENSOR-UPDATE-001")
    db.add(sensor)
    await db.flush()
    await db.commit()

    response = await client.put(
        f"/surveillance_monitoring_ops/update_sensor_health/{sensor.id}",
        json={"status": "operational", "recovery_type": None},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "operational"


@pytest.mark.anyio
async def test_update_sensor_health_transition_creates_notification(client, db):
    """Status change should create a failure notification record."""
    sensor = SurveillanceSensor(sensor_identifier="SENSOR-NOTIFY-001")
    db.add(sensor)
    await db.flush()

    # Initial health record
    health = SurveillanceSensorHealth(sensor_id=sensor.id, status="operational")
    db.add(health)
    await db.flush()
    await db.commit()

    # Transition to degraded
    response = await client.put(
        f"/surveillance_monitoring_ops/update_sensor_health/{sensor.id}",
        json={"status": "degraded", "recovery_type": "automatic"},
    )
    assert response.status_code == 200

    # Check notifications list
    notif_resp = await client.get("/surveillance_monitoring_ops/list_sensor_health_notifications")
    assert notif_resp.status_code == 200
    notifications = notif_resp.json()
    assert len(notifications) >= 1
    assert notifications[0]["previous_status"] == "operational"
    assert notifications[0]["new_status"] == "degraded"


# ══════════════════════════════════════════════════════════════════════════════
# Conformance with data
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_conformance_summary_with_conforming_records(client, db):
    """Summary with conforming records should reflect correct counts and rate."""
    for _ in range(3):
        db.add(ConformanceRecord(conformance_state=1))
    db.add(ConformanceRecord(conformance_state=0))
    await db.flush()
    await db.commit()

    response = await client.get("/conformance_monitoring_ops/conformance_record_summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_records"] == 4
    assert body["conforming_records"] == 3
    assert body["non_conforming_records"] == 1
    assert abs(body["conformance_rate_percent"] - 75.0) < 0.1


@pytest.mark.anyio
async def test_conformance_status_with_nonconforming(client, db):
    """Status should show is_conforming=False when there are unresolved non-conforming records."""
    db.add(ConformanceRecord(conformance_state=0, resolved=False))
    await db.flush()
    await db.commit()

    response = await client.get("/conformance_monitoring_ops/conformance_status")
    assert response.status_code == 200
    body = response.json()
    assert body["is_conforming"] is False
    assert body["active_nonconforming_count"] >= 1


@pytest.mark.anyio
async def test_get_conformance_records_returns_data(client, db):
    """Records endpoint should return inserted records."""
    db.add(ConformanceRecord(conformance_state=1, event_type="GPS_LOSS"))
    db.add(ConformanceRecord(conformance_state=0, event_type="GEOFENCE_BREACH"))
    await db.flush()
    await db.commit()

    response = await client.get("/conformance_monitoring_ops/get_conformance_records")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2


# ══════════════════════════════════════════════════════════════════════════════
# Constraint with data
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_get_constraint_detail_found(client, db):
    """Should return constraint detail when it exists."""
    detail = ConstraintDetail(volumes="{}", _type="airspace")
    db.add(detail)
    await db.flush()
    await db.commit()

    response = await client.get(f"/constraint_ops/constraint_detail/{detail.id}")
    assert response.status_code == 200
    body = response.json()
    assert str(body["id"]) == str(detail.id)
    assert body["type"] == "airspace"


@pytest.mark.anyio
async def test_get_constraint_reference_found(client, db):
    """Should return constraint reference when it exists."""
    ref = ConstraintReference(uss_availability="Unknown", uss_base_url="https://example.com")
    db.add(ref)
    await db.flush()
    await db.commit()

    response = await client.get(f"/constraint_ops/constraint_reference/{ref.id}")
    assert response.status_code == 200
    body = response.json()
    assert str(body["id"]) == str(ref.id)
    assert body["uss_availability"] == "Unknown"
