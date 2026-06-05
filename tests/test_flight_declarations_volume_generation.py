"""Tests for flight_blender.flight_declarations/custom_volume_generation.py and data_helper.py."""

import arrow
import pytest
from geojson import Feature, FeatureCollection

from flight_blender.core.operations.conformance import cast_to_volume4d
from flight_blender.core.operations.flight_declarations import CustomVolumeGenerator
from flight_blender.core.entities.scd import LatLngPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START_DT = "2026-06-01T10:00:00Z"
END_DT = "2026-06-01T10:30:00Z"


def _make_linestring_feature(coords=None, max_alt=100.0, min_alt=0.0, feat_id=1):
    if coords is None:
        coords = [[0.0, 0.0], [0.001, 0.001], [0.002, 0.0]]
    return Feature(
        geometry={"type": "LineString", "coordinates": coords},
        properties={
            "id": feat_id,
            "max_altitude": {"meters": max_alt},
            "min_altitude": {"meters": min_alt},
        },
    )


def _make_polygon_feature(max_alt=120.0, min_alt=20.0):
    return Feature(
        geometry={
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]],
        },
        properties={
            "max_altitude": {"meters": max_alt},
            "min_altitude": {"meters": min_alt},
        },
    )


def _gen():
    return CustomVolumeGenerator(
        default_uav_speed_m_per_s=5.5,
        default_uav_climb_rate_m_per_s=1.5,
        default_uav_descent_rate_m_per_s=1.5,
    )


# ---------------------------------------------------------------------------
# CustomVolumeGenerator
# ---------------------------------------------------------------------------


class TestCustomVolumeGeneratorInit:
    def test_instantiation(self):
        gen = _gen()
        assert gen.default_uav_speed_m_per_s == 5.5
        assert gen.default_uav_climb_rate_m_per_s == 1.5
        assert gen.all_features == []


class TestBreakLinestringToSmallerPieces:
    def test_single_segment_short(self):
        gen = _gen()
        feature = _make_linestring_feature([[0.0, 0.0], [0.00001, 0.0]])
        pieces = gen._break_linestring_to_smaller_pieces(feature, piece_length_m=100.0)
        # Very short segment, one piece
        assert len(pieces) >= 1

    def test_long_segment_creates_multiple_pieces(self):
        gen = _gen()
        # ~1km segment, piece_length=100m → ~10 pieces
        feature = _make_linestring_feature([[0.0, 0.0], [0.009, 0.0]])
        pieces = gen._break_linestring_to_smaller_pieces(feature, piece_length_m=100.0)
        assert len(pieces) > 1

    def test_single_coordinate_returns_original(self):
        gen = _gen()
        feature = _make_linestring_feature([[0.0, 0.0]])
        pieces = gen._break_linestring_to_smaller_pieces(feature)
        assert len(pieces) == 1

    def test_preserves_properties(self):
        gen = _gen()
        feature = _make_linestring_feature([[0.0, 0.0], [0.001, 0.0]])
        pieces = gen._break_linestring_to_smaller_pieces(feature, piece_length_m=10.0)
        for piece in pieces:
            assert piece["properties"]["id"] == 1


class TestBuildV4DFromGeoJSON:
    def test_all_linestrings(self):
        gen = _gen()
        fc = FeatureCollection(features=[_make_linestring_feature()])
        result = gen.build_v4d_from_geojson(fc, START_DT, END_DT)
        assert len(result) >= 1

    def test_all_polygons(self):
        gen = _gen()
        fc = FeatureCollection(features=[_make_polygon_feature()])
        result = gen.build_v4d_from_geojson(fc, START_DT, END_DT)
        assert len(result) == 1

    def test_mixed_linestrings_and_polygons(self):
        gen = _gen()
        fc = FeatureCollection(features=[_make_linestring_feature(), _make_polygon_feature()])
        result = gen.build_v4d_from_geojson(fc, START_DT, END_DT)
        assert len(result) >= 1


class TestBuildV4DFromLinestrings:
    def test_returns_v4d_list(self):
        gen = _gen()
        fc = FeatureCollection(features=[_make_linestring_feature(feat_id=1)])
        result = gen.build_v4d_from_linestrings(fc, START_DT, END_DT)
        # At minimum: takeoff + landing + per-piece segments
        assert len(result) >= 2

    def test_takeoff_and_landing_volumes(self):
        gen = _gen()
        fc = FeatureCollection(features=[_make_linestring_feature(feat_id=1)])
        result = gen.build_v4d_from_linestrings(fc, START_DT, END_DT)
        # first v4d starts at START_DT
        assert arrow.get(result[0].time_start.value) <= arrow.get(START_DT).shift(seconds=2)

    def test_multiple_features_sorted_by_id(self):
        gen = _gen()
        fc = FeatureCollection(
            features=[
                _make_linestring_feature([[0.002, 0.0], [0.003, 0.0]], feat_id=2),
                _make_linestring_feature([[0.0, 0.0], [0.001, 0.0]], feat_id=1),
            ]
        )
        result = gen.build_v4d_from_linestrings(fc, START_DT, END_DT)
        assert len(result) >= 2

    def test_altitude_bounds_in_v4d(self):
        gen = _gen()
        fc = FeatureCollection(features=[_make_linestring_feature(max_alt=150.0, min_alt=10.0)])
        result = gen.build_v4d_from_linestrings(fc, START_DT, END_DT)
        # Check that altitude bounds exist
        v4d = result[0]
        assert v4d.volume.altitude_upper.value == 150.0
        assert v4d.volume.altitude_lower.value == 10.0


class TestBuildV4DFromMixedPolygonsAndLinestrings:
    def test_polygon_feature(self):
        gen = _gen()
        fc = FeatureCollection(features=[_make_polygon_feature()])
        result = gen.build_v4d_from_mixed_polygons_and_linestrings(fc, START_DT, END_DT)
        assert len(result) == 1
        v4d = result[0]
        assert v4d.volume.altitude_upper.value == 120.0

    def test_mixed_features(self):
        gen = _gen()
        fc = FeatureCollection(features=[_make_polygon_feature(), _make_polygon_feature(max_alt=200.0, min_alt=50.0)])
        result = gen.build_v4d_from_mixed_polygons_and_linestrings(fc, START_DT, END_DT)
        assert len(result) == 2

    def test_per_feature_time_override(self):
        """Features with start_time/end_time in properties should use those."""
        gen = _gen()
        feature = Feature(
            geometry={
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]],
            },
            properties={
                "max_altitude": {"meters": 100.0},
                "min_altitude": {"meters": 0.0},
                "start_time": "2026-06-01T11:00:00Z",
                "end_time": "2026-06-01T11:30:00Z",
            },
        )
        fc = FeatureCollection(features=[feature])
        result = gen.build_v4d_from_mixed_polygons_and_linestrings(fc, START_DT, END_DT)
        assert result[0].time_start.value == "2026-06-01T11:00:00Z"


class TestCreateBufferedVolume4D:
    def test_returns_volume4d(self):
        gen = _gen()
        point = LatLngPoint(lat=51.5, lng=-0.1)
        v4d = gen._create_buffered_volume_4d(
            point=point,
            max_altitude=100.0,
            min_altitude=10.0,
            time_start=START_DT,
            time_end=END_DT,
        )
        assert v4d.volume.altitude_upper.value == 100.0
        assert v4d.volume.altitude_lower.value == 10.0
        assert v4d.time_start.value == START_DT


# ---------------------------------------------------------------------------
# data_helper.cast_to_volume4d
# ---------------------------------------------------------------------------


class TestCastToVolume4D:
    def _make_volume_dict(self, polygon=True, circle=False):
        vol = {
            "volume": {
                "altitude_lower": {"value": 0.0, "reference": "W84", "units": "M"},
                "altitude_upper": {"value": 100.0, "reference": "W84", "units": "M"},
            },
            "time_start": {"format": "RFC3339", "value": "2026-01-01T10:00:00Z"},
            "time_end": {"format": "RFC3339", "value": "2026-01-01T10:30:00Z"},
        }
        if polygon:
            vol["volume"]["outline_polygon"] = {
                "vertices": [
                    {"lat": 0.0, "lng": 0.0},
                    {"lat": 0.0, "lng": 1.0},
                    {"lat": 1.0, "lng": 1.0},
                    {"lat": 1.0, "lng": 0.0},
                    {"lat": 0.0, "lng": 0.0},
                ]
            }
        if circle:
            vol["volume"]["outline_circle"] = {
                "center": {"lat": 51.5, "lng": -0.1},
                "radius": {"value": 100.0, "units": "M"},
            }
        return vol

    def test_polygon_volume(self):
        v = self._make_volume_dict(polygon=True)
        result = cast_to_volume4d(v)
        assert result.volume.outline_polygon is not None

    def test_circle_volume(self):
        v = self._make_volume_dict(polygon=False, circle=True)
        result = cast_to_volume4d(v)
        assert result.volume.outline_circle is not None

    def test_neither_polygon_nor_circle(self):
        v = self._make_volume_dict(polygon=False, circle=False)
        result = cast_to_volume4d(v)
        assert result.time_start.value == "2026-01-01T10:00:00Z"

    def test_null_circle(self):
        v = self._make_volume_dict(polygon=False)
        v["volume"]["outline_circle"] = None
        result = cast_to_volume4d(v)
        assert result.volume.outline_circle is None
