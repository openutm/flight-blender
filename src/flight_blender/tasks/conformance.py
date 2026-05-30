"""
Celery tasks for conformance monitoring.

These tasks load the persisted flight declaration, geofences and live
telemetry, then delegate the actual C2-C11 decision logic to the pure
conformance engine in :mod:`flight_blender.services.conformance_engine`.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from flight_blender.common.enums import ACTIVE_OPERATIONAL_STATES
from flight_blender.common.redis_stream_operations import read_latest_observation
from flight_blender.config import get_settings
from flight_blender.models.conformance import ConformanceRecord
from flight_blender.models.flight_declaration import FlightDeclaration
from flight_blender.models.geo_fence import GeoFence
from flight_blender.services.conformance_engine import (
    CONFORMANCE_CHECK_LABELS,
    ConformanceCheck,
    GeoFencePolygon,
    OperationalIntentSnapshot,
    TelemetryObservation,
    VolumeBound,
    check_operation_conformant_via_telemetry,
    check_operational_intent_reference_conformance,
)
from flight_blender.tasks.celery_app import celery_app


def _sync_engine():
    settings = get_settings()
    sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
    return create_engine(sync_url)


def _ussp_network_enabled() -> bool:
    return bool(int(os.environ.get("USSP_NETWORK_ENABLED", 0)))


def _record_from_result(
    declaration_id: uuid.UUID,
    result: ConformanceCheck,
    event_type: str,
) -> ConformanceRecord:
    """Map an engine result onto a persisted ``ConformanceRecord``."""
    is_conforming = result is ConformanceCheck.CONFORMANT
    label = CONFORMANCE_CHECK_LABELS.get(result, "Unknown")
    if is_conforming:
        description = "Conformance check passed"
    else:
        description = f"Conformance check failed: {result.name} ({label})"
    return ConformanceRecord(
        flight_declaration_id=declaration_id,
        conformance_state=1 if is_conforming else 0,
        description=description,
        event_type=event_type,
        geofence_breach=result is ConformanceCheck.C8,
        resolved=is_conforming,
    )


def _volumes_from_operational_intent(operational_intent_raw: str) -> list[VolumeBound]:
    """Parse the declaration's operational_intent JSON into engine VolumeBounds.

    Faithful to the Django ``cast_to_volume4d`` shape:
    ``volume["volume"]["outline_polygon"]["vertices"]`` (lng/lat),
    ``volume["volume"]["altitude_lower"|"altitude_upper"]["value"]``.
    """
    if not operational_intent_raw:
        return []
    try:
        details = json.loads(operational_intent_raw)
    except (json.JSONDecodeError, TypeError):
        return []

    bounds: list[VolumeBound] = []
    for vol in details.get("volumes", []) or []:
        volume = vol.get("volume", {})
        outline = volume.get("outline_polygon") or {}
        vertices = [(float(v["lng"]), float(v["lat"])) for v in outline.get("vertices", [])]
        if len(vertices) < 3:
            continue
        altitude_lower = float((volume.get("altitude_lower") or {}).get("value", 0.0))
        altitude_upper = float((volume.get("altitude_upper") or {}).get("value", 0.0))
        bounds.append(
            VolumeBound(
                vertices=vertices,
                altitude_lower=altitude_lower,
                altitude_upper=altitude_upper,
            )
        )
    return bounds


def _rings_from_geometry(geometry: dict) -> list[list[tuple[float, float]]]:
    """Extract outer-ring (lng, lat) vertex lists from a GeoJSON geometry."""
    gtype = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if gtype == "Polygon" and coordinates:
        return [[(c[0], c[1]) for c in coordinates[0]]]
    if gtype == "MultiPolygon":
        return [[(c[0], c[1]) for c in polygon[0]] for polygon in coordinates if polygon]
    return []


def _geofences_from_records(geofences) -> list[GeoFencePolygon]:
    """Convert GeoFence rows (GeoJSON) into engine GeoFencePolygon objects."""
    polygons: list[GeoFencePolygon] = []
    for geofence in geofences:
        raw = getattr(geofence, "raw_geo_fence", None)
        if not raw:
            continue
        try:
            geojson = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for feature in geojson.get("features", []):
            for ring in _rings_from_geometry(feature.get("geometry", {})):
                polygons.append(GeoFencePolygon(vertices=ring))
    return polygons


def _parse_telemetry(observation: dict) -> TelemetryObservation | None:
    """Build a :class:`TelemetryObservation` from a Redis-stream observation dict.

    The FastAPI flight-feed stream stores ``lat_dd`` / ``lon_dd`` (decimal
    degrees), ``altitude_mm`` (millimetres) and ``icao_address``. Returns
    ``None`` when the required position fields are missing.
    """
    try:
        lat = float(observation["lat_dd"])
        lng = float(observation["lon_dd"])
    except (KeyError, TypeError, ValueError):
        return None
    try:
        altitude_mm = float(observation.get("altitude_mm", 0.0) or 0.0)
    except (TypeError, ValueError):
        altitude_mm = 0.0
    return TelemetryObservation(
        aircraft_id=str(observation.get("icao_address", "")),
        lat=lat,
        lng=lng,
        # stream stores millimetres; the engine works in metres WGS84.
        altitude_m_wgs_84=altitude_mm / 1000.0,
    )


@celery_app.task(name="check_all_flight_conformance")
def check_all_flight_conformance():
    """Periodic dispatcher: fan out a conformance check to every active operation.

    This is the FastAPI replacement for the dropped Django ``TaskScheduler``;
    it is invoked on a fixed Celery-beat cadence and enqueues a per-declaration
    :func:`check_flight_conformance` for each operation in an active state
    (Activated / Nonconforming / Contingent).
    """
    active_states = [int(s) for s in ACTIVE_OPERATIONAL_STATES]
    engine = _sync_engine()
    with Session(engine) as session:
        declarations = session.execute(select(FlightDeclaration).where(FlightDeclaration.state.in_(active_states))).scalars().all()
        for decl in declarations:
            check_flight_conformance.delay(str(decl.id))
    logger.info("Dispatched conformance checks for active operations")


@celery_app.task(name="check_flight_conformance", bind=True, max_retries=2)
def check_flight_conformance(self, flight_declaration_id: str):
    """Telemetry-independent conformance (C9-C11): liveness and state checks.

    Creates a ``ConformanceRecord`` capturing whether the operation's
    operational-intent reference is conformant.
    """
    try:
        engine = _sync_engine()
        with Session(engine) as session:
            decl = session.get(FlightDeclaration, uuid.UUID(flight_declaration_id))
            if not decl:
                logger.error("Declaration %s not found for conformance check", flight_declaration_id)
                return

            result = check_operational_intent_reference_conformance(
                state=decl.state,
                latest_telemetry_datetime=decl.latest_telemetry_datetime,
                operational_intent_reference_exists=True,
                ussp_network_enabled=_ussp_network_enabled(),
            )
            record = _record_from_result(decl.id, result, event_type="scheduled_check")
            session.add(record)
            session.commit()
            logger.info(
                "Conformance check for %s: %s",
                flight_declaration_id,
                "conforming" if result is ConformanceCheck.CONFORMANT else result.name,
            )
    except Exception as exc:
        logger.error("Conformance check error: %s", exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="check_operation_telemetry_conformance", bind=True, max_retries=2)
def check_operation_telemetry_conformance(self, flight_declaration_id: str):
    """Telemetry-driven conformance (C2-C8) for the latest observation."""
    try:
        latest = read_latest_observation(session_id=flight_declaration_id)
        if not latest:
            logger.debug("No telemetry for %s", flight_declaration_id)
            return

        engine = _sync_engine()
        with Session(engine) as session:
            decl = session.get(FlightDeclaration, uuid.UUID(flight_declaration_id))
            if not decl:
                logger.error("Declaration %s not found for telemetry conformance check", flight_declaration_id)
                return

            telemetry = _parse_telemetry(latest)
            if telemetry is None:
                logger.debug("Telemetry for %s missing position fields", flight_declaration_id)
                return

            now = datetime.now(timezone.utc)
            active_geofences = session.execute(select(GeoFence).where(GeoFence.start_datetime <= now, GeoFence.end_datetime >= now)).scalars().all()

            snapshot = OperationalIntentSnapshot(
                aircraft_id=decl.aircraft_id,
                state=decl.state,
                start_datetime=decl.start_datetime,
                end_datetime=decl.end_datetime,
                volumes=_volumes_from_operational_intent(decl.operational_intent),
                operational_intent_reference_exists=True,
            )

            result = check_operation_conformant_via_telemetry(
                snapshot=snapshot,
                telemetry=telemetry,
                geofences=_geofences_from_records(active_geofences),
                now=now,
                ussp_network_enabled=_ussp_network_enabled(),
            )
            record = _record_from_result(decl.id, result, event_type="telemetry_check")
            session.add(record)
            session.commit()
            logger.info(
                "Telemetry conformance check for %s: %s",
                flight_declaration_id,
                "conforming" if result is ConformanceCheck.CONFORMANT else result.name,
            )
    except Exception as exc:
        logger.error("Telemetry conformance check error: %s", exc)
        raise self.retry(exc=exc, countdown=30)
