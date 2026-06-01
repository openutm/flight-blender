"""
Celery tasks and pure-Python GeoZone parsing for geo fence / geozone operations.

The parsing pipeline ports the Django ``geo_fence_operations`` behaviour:

* ED-269 ``UASZoneList`` features are read with the correct ``upperLimit`` /
  ``lowerLimit`` field names.
* ``Circle`` horizontal projections are buffered into a polygon ring (pure
  Python, no shapely dependency).
* Real bounding boxes are computed and stored as a comma-separated
  ``"minx,miny,maxx,maxy"`` string (matching the Django ``unary_union(...).bounds``
  formatting), and the raw GeoZone document is persisted on ``geozone``.
* Each persisted ``GeoFence`` is flagged ``is_test_dataset=True`` because the
  geozone ingest path feeds the InterUSS geo-awareness test harness.
"""

import json
import math
from datetime import datetime, timezone

import requests
from loguru import logger

from flight_blender.common.geometry import compute_bounds
from flight_blender.tasks.celery_app import celery_app

# ── GeoZone source status store ──────────────────────────────────────────────
# The InterUSS qualifier PUTs a geospatial data source, then polls its status.
# We mirror the Django Redis bookkeeping but degrade gracefully to an in-process
# dict when Redis is unavailable (e.g. tests, local dev without a broker).

_STATUS_KEY_PREFIX = "geoawareness_test."
_in_memory_status: dict[str, dict] = {}


def _get_redis():
    """Return a connected Redis client, or ``None`` if Redis is unavailable."""
    try:
        from flight_blender.common.redis_client import get_redis

        client = get_redis()
        client.ping()
        return client
    except Exception:  # noqa: BLE001 - any failure means "no Redis"; degrade
        return None


def set_geozone_source_status(geozone_source_id: str, status: dict, ttl: int = 3000) -> None:
    """Persist the import status for a geozone source id."""
    key = _STATUS_KEY_PREFIX + str(geozone_source_id)
    payload = json.dumps(status)
    client = _get_redis()
    if client is not None:
        try:
            client.set(key, payload)
            client.expire(name=key, time=ttl)
            return
        except Exception:  # noqa: BLE001 - fall back to memory
            pass
    _in_memory_status[key] = status


def get_geozone_source_status(geozone_source_id: str) -> dict | None:
    """Return the stored import status for a geozone source id, or ``None``."""
    key = _STATUS_KEY_PREFIX + str(geozone_source_id)
    client = _get_redis()
    if client is not None:
        try:
            if client.exists(key):
                return json.loads(client.get(key))
            return None
        except Exception:  # noqa: BLE001 - fall back to memory
            pass
    return _in_memory_status.get(key)


def delete_geozone_source_status(geozone_source_id: str) -> bool:
    """Delete the stored status. Returns True if a record existed."""
    key = _STATUS_KEY_PREFIX + str(geozone_source_id)
    client = _get_redis()
    if client is not None:
        try:
            if client.exists(key):
                client.delete(key)
                return True
            return False
        except Exception:  # noqa: BLE001 - fall back to memory
            pass
    return _in_memory_status.pop(key, None) is not None


# ── Pure-Python geometry helpers ─────────────────────────────────────────────

_EARTH_RADIUS_M = 6378137.0


def geodesic_circle_to_ring(center_lon: float, center_lat: float, radius_m: float, segments: int = 32) -> list[list[float]]:
    """Approximate a geodesic circle as a closed polygon ring of ``[lon, lat]`` pairs.

    Pure-Python replacement for the Django ``geodesic_point_buffer`` (shapely +
    pyproj). Accurate enough for bounding-box computation and point-in-polygon
    membership at the small radii used by geo-awareness zones.
    """
    ring: list[list[float]] = []
    lat_rad = math.radians(center_lat)
    d_lat = (radius_m / _EARTH_RADIUS_M) * (180.0 / math.pi)
    cos_lat = math.cos(lat_rad) or 1e-9
    d_lon = (radius_m / (_EARTH_RADIUS_M * cos_lat)) * (180.0 / math.pi)
    for i in range(segments):
        theta = 2.0 * math.pi * (i / segments)
        ring.append([center_lon + d_lon * math.cos(theta), center_lat + d_lat * math.sin(theta)])
    ring.append(ring[0])
    return ring


def _flatten_coordinates(coords) -> list[list[float]]:
    """Recursively flatten arbitrarily nested coordinate arrays to ``[lon, lat]`` pairs."""
    flat: list[list[float]] = []
    if not isinstance(coords, (list, tuple)):
        return flat
    if len(coords) >= 2 and all(isinstance(c, (int, float)) for c in coords[:2]):
        flat.append([float(coords[0]), float(coords[1])])
        return flat
    for item in coords:
        flat.extend(_flatten_coordinates(item))
    return flat


def feature_to_coordinates(feature: dict) -> list[list[float]]:
    """Extract ``[lon, lat]`` pairs from an ED-269 UASZone feature.

    Supports both ``geometry: [{horizontalProjection: {...}}]`` (ED-269) and a
    plain GeoJSON ``geometry`` object. ``Circle`` projections are buffered into a
    polygon ring.
    """
    geometries = feature.get("geometry")
    coords: list[list[float]] = []
    if isinstance(geometries, dict):
        geometries = [{"horizontalProjection": geometries}]
    if not isinstance(geometries, list):
        return coords
    for geom in geometries:
        proj = geom.get("horizontalProjection", geom) if isinstance(geom, dict) else {}
        gtype = proj.get("type")
        if gtype == "Circle":
            center = proj.get("center", [])
            radius = float(proj.get("radius", 0) or 0)
            if len(center) >= 2:
                coords.extend(geodesic_circle_to_ring(float(center[0]), float(center[1]), radius))
        else:
            coords.extend(_flatten_coordinates(proj.get("coordinates", [])))
    return coords


def _parse_dt(value, fallback: datetime) -> datetime:
    from flight_blender.common.datetime_utils import parse_iso_utc

    return parse_iso_utc(value, fallback=fallback) or fallback


def validate_geo_zone(geo_zone) -> bool:
    """Validate a GeoZone payload (Django ``common.validate_geo_zone`` parity).

    Accepts a dict or a JSON string. A valid GeoZone is a dict carrying a
    non-empty feature list (``UASZoneList`` or ``features``) where each feature is
    a dict that describes itself in some way: a geometry, an identifier/name, or a
    zone authority. Empty or non-dict payloads are rejected.
    """
    if isinstance(geo_zone, str):
        try:
            geo_zone = json.loads(geo_zone)
        except (TypeError, ValueError):
            return False
    if not isinstance(geo_zone, dict):
        return False
    features = _geo_zone_features(geo_zone)
    if not features or not isinstance(features, list):
        return False
    descriptors = ("geometry", "name", "identifier", "title", "zoneAuthority", "applicability")
    return all(isinstance(f, dict) and any(f.get(k) for k in descriptors) for f in features)


def _geo_zone_features(geo_zone: dict) -> list:
    """Return the feature list under any of the accepted GeoZone keys."""
    return geo_zone.get("UASZoneList") or geo_zone.get("features") or geo_zone.get("GeoZones") or []


def parse_geo_zone_features(geo_zone: dict) -> list[dict]:
    """Parse an ED-269 GeoZone document into per-feature persistence dicts."""
    features = _geo_zone_features(geo_zone)
    now = datetime.now(timezone.utc)
    parsed: list[dict] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        coordinates = feature_to_coordinates(feature)
        bounds = compute_bounds(coordinates)
        applicability = feature.get("applicability") or {}
        if isinstance(applicability, list):
            applicability = applicability[0] if applicability else {}
        start_dt = _parse_dt(applicability.get("startDateTime"), now)
        end_dt = _parse_dt(applicability.get("endDateTime"), start_dt)
        name = feature.get("name") or feature.get("identifier") or feature.get("title") or "Unknown"
        parsed.append(
            {
                "geozone": json.dumps(feature),
                "raw_geo_fence": json.dumps(geo_zone),
                "upper_limit": float(feature.get("upperLimit", feature.get("upper_limit", feature.get("upper_limit_m", 120))) or 0),
                "lower_limit": float(feature.get("lowerLimit", feature.get("lower_limit", feature.get("lower_limit_m", 0))) or 0),
                "altitude_ref": 0,
                "name": str(name)[:50],
                "bounds": bounds,
                "status": 1,
                "is_test_dataset": True,
                "start_datetime": start_dt,
                "end_datetime": end_dt,
            }
        )
    return parsed


# ── Celery tasks ─────────────────────────────────────────────────────────────


@celery_app.task(name="download_geozone_source", bind=True, max_retries=3)
def download_geozone_source(self, geo_zone_url: str, geozone_source_id: str = ""):
    """Download a GeoZone source from a URL, parse it, and record import status."""
    try:
        resp = requests.get(geo_zone_url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        write_geo_zone.delay(data)
        if geozone_source_id:
            set_geozone_source_status(geozone_source_id, {"result": "Ready", "message": ""})
    except Exception as exc:  # noqa: BLE001
        logger.error("GeoZone download error from {}: {}", geo_zone_url, exc)
        if geozone_source_id:
            set_geozone_source_status(geozone_source_id, {"result": "Error", "message": str(exc)})
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="write_geo_zone", bind=True, max_retries=2)
def write_geo_zone(self, geozone_data, source_url: str = ""):
    """Parse an ED-269 GeoZone document and persist one GeoFence per feature."""
    try:
        # Import inside the task so tests can patch ``sqlalchemy.create_engine`` /
        # ``sqlalchemy.orm.Session`` (matches the other Celery tasks).
        from sqlalchemy.orm import Session

        from flight_blender.common.sync_engine import get_sync_engine
        from flight_blender.config import get_settings
        from flight_blender.models.geo_fence import GeoFence

        if isinstance(geozone_data, str):
            geozone_data = json.loads(geozone_data)

        parsed = parse_geo_zone_features(geozone_data)
        if not parsed:
            logger.warning("No GeoZone features found in payload from {}", source_url)
            return

        engine = get_sync_engine(get_settings().database_url)
        with Session(engine) as session:
            for fields in parsed:
                session.add(GeoFence(**fields))
            session.commit()
            logger.info("Created {} GeoFence records from GeoZone source", len(parsed))
    except Exception as exc:  # noqa: BLE001
        logger.error("GeoZone write error: {}", exc)
        raise self.retry(exc=exc, countdown=10)
