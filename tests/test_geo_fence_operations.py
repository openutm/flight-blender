"""FastAPI tests for geo_fence_ops endpoints."""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import arrow
import jwt
import pytest
from tests.conftest import (
    GA_TEST_SCOPE,
    READ_SCOPE,
    WRITE_SCOPE,
    auth_header as _django_auth_header,
)


def _fastapi_auth(scopes: list[str]) -> dict[str, str]:
    """Return Authorization header for FastAPI TestClient."""
    payload = {
        "sub": "test-user",
        "iss": "dummy",
        "aud": "testflight.flightblender.com",
        "scope": " ".join(scopes),
    }
    token = jwt.encode(payload, "secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


GEO_FENCE_PAYLOAD = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [13.4, 52.5],
                        [13.41, 52.5],
                        [13.41, 52.51],
                        [13.4, 52.51],
                        [13.4, 52.5],
                    ]
                ],
            },
            "properties": {
                "name": "Test GeoFence",
                "upper_limit": 50,
                "lower_limit": 20,
            },
        }
    ],
}


class TestGeoFenceCRUD:
    def test_list_geo_fences_empty(self, fastapi_client):
        resp = fastapi_client.get("/geo_fence_ops/geo_fence", headers=_fastapi_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["count"] == 0

    def test_set_geo_fence_success(self, fastapi_client):
        resp = fastapi_client.put(
            "/geo_fence_ops/set_geo_fence",
            json=GEO_FENCE_PAYLOAD,
            headers=_fastapi_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["message"] == "Geofence Declaration submitted"

    def test_set_geo_fence_unsupported_media_type(self, fastapi_client):
        resp = fastapi_client.put(
            "/geo_fence_ops/set_geo_fence",
            content="{}",
            headers={**_fastapi_auth(WRITE_SCOPE), "content-type": "text/plain"},
        )
        assert resp.status_code == 415

    def test_set_geo_fence_with_start_end_time(self, fastapi_client):
        """Regression: payload with start_time/end_time in properties must return 200, not 500."""
        payload = {
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
        resp = fastapi_client.put("/geo_fence_ops/set_geo_fence", json=payload, headers=_fastapi_auth(WRITE_SCOPE))
        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_set_geo_fence_invalid_schema(self, fastapi_client):
        resp = fastapi_client.put(
            "/geo_fence_ops/set_geo_fence",
            json={"type": "FeatureCollection", "features": []},
            headers=_fastapi_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_geo_fence_not_found(self, fastapi_client):
        import uuid

        resp = fastapi_client.get(
            f"/geo_fence_ops/geo_fence/{uuid.uuid4()}",
            headers=_fastapi_auth(READ_SCOPE),
        )
        assert resp.status_code == 404

    def test_delete_geo_fence_not_found(self, fastapi_client):
        import uuid

        resp = fastapi_client.delete(
            f"/geo_fence_ops/geo_fence/{uuid.uuid4()}/delete",
            headers=_fastapi_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 404

    def test_create_then_get_then_delete(self, fastapi_client):
        # create
        resp = fastapi_client.put(
            "/geo_fence_ops/set_geo_fence",
            json=GEO_FENCE_PAYLOAD,
            headers=_fastapi_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        fence_id = resp.json()["id"]

        # get
        resp = fastapi_client.get(f"/geo_fence_ops/geo_fence/{fence_id}", headers=_fastapi_auth(READ_SCOPE))
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test GeoFence"

        # list — should appear
        resp = fastapi_client.get("/geo_fence_ops/geo_fence", headers=_fastapi_auth(READ_SCOPE))
        assert resp.status_code == 200

        # delete
        resp = fastapi_client.delete(f"/geo_fence_ops/geo_fence/{fence_id}/delete", headers=_fastapi_auth(WRITE_SCOPE))
        assert resp.status_code == 204

        # gone
        resp = fastapi_client.get(f"/geo_fence_ops/geo_fence/{fence_id}", headers=_fastapi_auth(READ_SCOPE))
        assert resp.status_code == 404


class TestGeoFenceAuthEnforcement:
    """Auth enforcement — BYPASS_AUTH_TOKEN_VERIFICATION must be True (default in tests)."""

    def test_missing_token_returns_401(self, fastapi_client):
        resp = fastapi_client.get("/geo_fence_ops/geo_fence")
        assert resp.status_code == 401

    def test_wrong_scope_returns_403(self, fastapi_client):
        resp = fastapi_client.get(
            "/geo_fence_ops/geo_fence",
            headers=_fastapi_auth(["wrong.scope"]),
        )
        assert resp.status_code == 403

    def test_read_scope_allows_list(self, fastapi_client):
        resp = fastapi_client.get("/geo_fence_ops/geo_fence", headers=_fastapi_auth(READ_SCOPE))
        assert resp.status_code == 200

    def test_read_scope_rejected_for_write(self, fastapi_client):
        resp = fastapi_client.put(
            "/geo_fence_ops/set_geo_fence",
            json=GEO_FENCE_PAYLOAD,
            headers=_fastapi_auth(READ_SCOPE),
        )
        assert resp.status_code == 403


class TestGeoAwarenessTestHarness:
    def test_status_endpoint(self, fastapi_client):
        resp = fastapi_client.get(
            "/geo_fence_ops/geo_awareness/status",
            headers=_fastapi_auth(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Ready"

    def test_geospatial_data_sources_empty(self, fastapi_client):
        resp = fastapi_client.get(
            "/geo_fence_ops/geo_awareness/geospatial_data_sources",
            headers=_fastapi_auth(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    def test_geozone_source_not_found(self, fastapi_client):
        resp = fastapi_client.get(
            "/geo_fence_ops/geo_awareness/geospatial_data_sources/nonexistent-id",
            headers=_fastapi_auth(GA_TEST_SCOPE),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Spatial geo fence additional coverage
# ---------------------------------------------------------------------------


class TestSpatialGeoFenceCoverage:
    """Additional tests for spatial_geo_fence."""

    def test_to_from_utm_polygon(self):
        """Test toFromUTM with Polygon."""
        from shapely.geometry import Polygon as ShpPolygon
        from flight_blender.utils.spatial_geo_fence import toFromUTM
        import pyproj

        proj = pyproj.Proj(proj="utm", zone=32, ellps="WGS84")
        polygon = ShpPolygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])

        result = toFromUTM(polygon, proj)

        assert result is not None

    def test_to_from_utm_point(self):
        """Test toFromUTM with Point."""
        from shapely.geometry import Point
        from flight_blender.utils.spatial_geo_fence import toFromUTM
        import pyproj

        proj = pyproj.Proj(proj="utm", zone=32, ellps="WGS84")
        point = Point(0, 0)

        result = toFromUTM(point, proj)

        assert result is not None

    def test_convert_shapely_to_geojson(self):
        """Test convert_shapely_to_geojson."""
        from shapely.geometry import Polygon as ShpPolygon
        from flight_blender.utils.spatial_geo_fence import convert_shapely_to_geojson

        polygon = ShpPolygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])

        result = convert_shapely_to_geojson(polygon)

        assert isinstance(result, str)
        assert "Polygon" in result

    def test_geo_fence_rtree_index_factory_add_box_to_index(self, tmp_path):
        """Test GeoFenceRTreeIndexFactory.add_box_to_index."""
        from flight_blender.utils.spatial_geo_fence import GeoFenceRTreeIndexFactory

        factory = GeoFenceRTreeIndexFactory(index_name=str(tmp_path / "geo-fence-index"))

        factory.add_box_to_index(
            id=1,
            geo_fence_id="test-fence-id",
            view=[0, 0, 1, 1],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )

        # No assertion needed, just ensure it doesn't raise

    def test_geo_fence_rtree_index_factory_delete_from_index(self, tmp_path):
        """Test GeoFenceRTreeIndexFactory.delete_from_index."""
        from flight_blender.utils.spatial_geo_fence import GeoFenceRTreeIndexFactory

        factory = GeoFenceRTreeIndexFactory(index_name=str(tmp_path / "geo-fence-index"))

        factory.add_box_to_index(
            id=1,
            geo_fence_id="test-fence-id",
            view=[0, 0, 1, 1],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )

        factory.delete_from_index(
            enumerated_id=1,
            view=[0, 0, 1, 1],
        )

        # No assertion needed, just ensure it doesn't raise

    def test_geo_fence_rtree_index_factory_generate_geo_fence_index(self, tmp_path):
        """Test GeoFenceRTreeIndexFactory.generate_geo_fence_index."""
        from flight_blender.utils.spatial_geo_fence import GeoFenceRTreeIndexFactory

        factory = GeoFenceRTreeIndexFactory(index_name=str(tmp_path / "geo-fence-index"))

        mock_fence = MagicMock()
        mock_fence.id = uuid.uuid4()
        mock_fence.bounds = "0,0,1,1"

        factory.generate_geo_fence_index([mock_fence])

        # No assertion needed, just ensure it doesn't raise

    def test_geo_fence_rtree_index_factory_check_box_intersection(self, tmp_path):
        """Test GeoFenceRTreeIndexFactory.check_box_intersection."""
        from flight_blender.utils.spatial_geo_fence import GeoFenceRTreeIndexFactory

        factory = GeoFenceRTreeIndexFactory(index_name=str(tmp_path / "geo-fence-index"))

        mock_fence = MagicMock()
        mock_fence.id = uuid.uuid4()
        mock_fence.bounds = "0,0,1,1"

        factory.generate_geo_fence_index([mock_fence])

        result = factory.check_box_intersection(view_box=[0, 0, 1, 1])

        assert isinstance(result, list)

    def test_geo_fence_rtree_index_factory_clear_rtree_index(self, tmp_path):
        """Test GeoFenceRTreeIndexFactory.clear_rtree_index."""
        from flight_blender.utils.spatial_geo_fence import GeoFenceRTreeIndexFactory

        factory = GeoFenceRTreeIndexFactory(index_name=str(tmp_path / "geo-fence-index"))

        mock_fence = MagicMock()
        mock_fence.id = uuid.uuid4()
        mock_fence.bounds = "0,0,1,1"

        factory.generate_geo_fence_index([mock_fence])

        factory.clear_rtree_index([mock_fence])

        # No assertion needed, just ensure it doesn't raise
# GeoFenceService additional coverage
# ---------------------------------------------------------------------------


class TestGeoFenceServiceCoverage:
    """Additional tests for GeoFenceOperations."""

    @pytest.mark.asyncio
    async def test_get_geofences_with_viewport(self):
        """Test get_geofences with viewport filter."""
        from flight_blender.services.geo_fence_svc import GeoFenceOperations

        mock_repo = AsyncMock()
        mock_dispatcher = MagicMock()
        mock_spatial = MagicMock()
        mock_redis = MagicMock()

        mock_fence = MagicMock()
        mock_fence.id = uuid.uuid4()
        mock_fence.name = "Test Fence"
        mock_fence.bounds = "0,0,1,1"
        mock_fence.start_datetime = arrow.utcnow().datetime
        mock_fence.end_datetime = arrow.utcnow().shift(days=1).datetime
        mock_fence.upper_limit = 1000
        mock_fence.lower_limit = 0
        mock_fence.is_test_dataset = False

        mock_repo.get_geofences_by_date_range = AsyncMock(return_value=[mock_fence])
        mock_spatial.filter_fences_by_viewport = MagicMock(return_value=[mock_fence])

        service = GeoFenceOperations(
            repo=mock_repo,
            dispatcher=mock_dispatcher,
            spatial=mock_spatial,
            redis=mock_redis,
        )

        result = await service.list_geofences(viewport="0,0,1,1")

        assert len(result) == 1
        mock_repo.get_geofences_by_date_range.assert_called_once()
        mock_spatial.filter_fences_by_viewport.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_geofence_returns_none_when_not_found(self):
        """Test get_geofence returns None when geofence not found."""
        from flight_blender.services.geo_fence_svc import GeoFenceOperations

        mock_repo = AsyncMock()
        mock_dispatcher = MagicMock()
        mock_spatial = MagicMock()
        mock_redis = MagicMock()

        mock_repo.get_by_id = AsyncMock(return_value=None)

        service = GeoFenceOperations(
            repo=mock_repo,
            dispatcher=mock_dispatcher,
            spatial=mock_spatial,
            redis=mock_redis,
        )

        result = await service.get_geofence(geofence_id=uuid.uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_create_geofence_from_feature_collection(self):
        """Test create_geofence_from_feature_collection."""
        from flight_blender.services.geo_fence_svc import GeoFenceOperations

        mock_repo = AsyncMock()
        mock_dispatcher = MagicMock()
        mock_spatial = MagicMock()
        mock_redis = MagicMock()

        mock_fence = MagicMock()
        mock_fence.id = uuid.uuid4()
        mock_repo.create = AsyncMock(return_value=mock_fence)

        service = GeoFenceOperations(
            repo=mock_repo,
            dispatcher=mock_dispatcher,
            spatial=mock_spatial,
            redis=mock_redis,
        )

        geo_fence_data = {
            "features": [{
                "type": "Feature",
                "properties": {
                    "name": "Test Fence",
                    "start_time": arrow.utcnow().isoformat(),
                    "end_time": arrow.utcnow().shift(days=1).isoformat(),
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
                },
            }]
        }

        result = await service.create_geofence_from_feature_collection(geo_fence_data)

        assert "id" in result
        mock_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_geofence(self):
        """Test delete_geofence."""
        from flight_blender.services.geo_fence_svc import GeoFenceOperations

        mock_repo = AsyncMock()
        mock_dispatcher = MagicMock()
        mock_spatial = MagicMock()
        mock_redis = MagicMock()

        mock_repo.delete = AsyncMock(return_value=True)

        service = GeoFenceOperations(
            repo=mock_repo,
            dispatcher=mock_dispatcher,
            spatial=mock_spatial,
            redis=mock_redis,
        )

        result = await service.delete_geofence(geofence_id=uuid.uuid4())

        assert result is True
        mock_repo.delete.assert_called_once()
