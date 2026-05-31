"""Unit tests for the GeoZone parsing pipeline (Django ``write_geo_zone`` parity).

Covers the pure-Python parsing helpers (no shapely):
- ED-269 ``upperLimit`` / ``lowerLimit`` field names
- Circle -> polygon ring buffering
- real bounding-box computation (comma-separated string)
- raw_geo_fence / geozone persistence fields and is_test_dataset flagging
- the request-time ``validate_geo_zone`` gate
"""

from __future__ import annotations

import json

from flight_blender.tasks.geo_fence import (
    compute_bounds,
    feature_to_coordinates,
    geodesic_circle_to_ring,
    parse_geo_zone_features,
    validate_geo_zone,
)

# A minimal ED-269 GeoZone with a Circle geometry.
CIRCLE_GEO_ZONE = {
    "title": "Test Zone",
    "description": "A test geozone",
    "UASZoneList": [
        {
            "identifier": "ZONE-1",
            "name": "Restricted Alpha",
            "upperLimit": 120,
            "lowerLimit": 0,
            "zoneAuthority": [{"name": "CAA"}],
            "applicability": {
                "startDateTime": "2023-01-01T00:00:00Z",
                "endDateTime": "2030-01-01T00:00:00Z",
            },
            "geometry": [{"horizontalProjection": {"type": "Circle", "center": [0.0, 51.5], "radius": 500}}],
        }
    ],
}

POLYGON_GEO_ZONE = {
    "title": "Poly Zone",
    "description": "Polygon geozone",
    "features": [
        {
            "name": "Poly Alpha",
            "upperLimit": 300,
            "lowerLimit": 50,
            "geometry": [
                {
                    "horizontalProjection": {
                        "type": "Polygon",
                        "coordinates": [[[0.0, 51.0], [1.0, 51.0], [1.0, 52.0], [0.0, 52.0], [0.0, 51.0]]],
                    }
                }
            ],
        }
    ],
}


def test_geodesic_circle_to_ring_is_closed_ring_around_center():
    ring = geodesic_circle_to_ring(0.0, 51.5, 500)
    assert len(ring) >= 4
    assert ring[0] == ring[-1]  # closed
    # All points should be near the center (within ~0.01 deg for a 500 m radius).
    for lon, lat in ring:
        assert abs(lon - 0.0) < 0.02
        assert abs(lat - 51.5) < 0.02


def test_feature_to_coordinates_circle_buffers_to_ring():
    coords = feature_to_coordinates(CIRCLE_GEO_ZONE["UASZoneList"][0])
    assert len(coords) >= 4
    # Center should sit inside the bbox of the buffered ring.
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    assert min(lons) < 0.0 < max(lons)
    assert min(lats) < 51.5 < max(lats)


def test_feature_to_coordinates_polygon():
    coords = feature_to_coordinates(POLYGON_GEO_ZONE["features"][0])
    assert [0.0, 51.0] in coords
    assert [1.0, 52.0] in coords


def test_compute_bounds_format_is_comma_separated_minx_miny_maxx_maxy():
    bounds = compute_bounds([[0.0, 51.0], [1.0, 52.0]])
    parts = bounds.split(",")
    assert len(parts) == 4
    minx, miny, maxx, maxy = (float(p) for p in parts)
    assert (minx, miny, maxx, maxy) == (0.0, 51.0, 1.0, 52.0)


def test_compute_bounds_empty_returns_empty_string():
    assert compute_bounds([]) == ""


def test_parse_geo_zone_features_reads_ed269_limits_and_sets_fields():
    parsed = parse_geo_zone_features(CIRCLE_GEO_ZONE)
    assert len(parsed) == 1
    feat = parsed[0]
    # ED-269 upperLimit / lowerLimit (NOT upper_limit_m / lower_limit_m).
    assert feat["upper_limit"] == 120.0
    assert feat["lower_limit"] == 0.0
    assert feat["name"] == "Restricted Alpha"
    # Real bounds were computed (non-empty, comma-separated).
    assert feat["bounds"] and len(feat["bounds"].split(",")) == 4
    # raw_geo_fence holds the whole document; geozone holds the feature.
    assert json.loads(feat["raw_geo_fence"])["title"] == "Test Zone"
    assert json.loads(feat["geozone"])["identifier"] == "ZONE-1"
    # GeoZone ingest feeds the test harness -> flagged as test dataset.
    assert feat["is_test_dataset"] is True
    assert feat["status"] == 1


def test_parse_geo_zone_features_polygon_limits():
    parsed = parse_geo_zone_features(POLYGON_GEO_ZONE)
    assert parsed[0]["upper_limit"] == 300.0
    assert parsed[0]["lower_limit"] == 50.0


# ── validate_geo_zone gate ──────────────────────────────────────────────────────


def test_validate_geo_zone_accepts_valid_payload():
    assert validate_geo_zone(CIRCLE_GEO_ZONE) is True
    assert validate_geo_zone(POLYGON_GEO_ZONE) is True


def test_validate_geo_zone_rejects_empty_features():
    assert validate_geo_zone({"title": "x", "description": "y", "UASZoneList": []}) is False


def test_validate_geo_zone_rejects_non_dict():
    assert validate_geo_zone("not a dict") is False
    assert validate_geo_zone(123) is False


def test_validate_geo_zone_accepts_json_string():
    assert validate_geo_zone(json.dumps(CIRCLE_GEO_ZONE)) is True
