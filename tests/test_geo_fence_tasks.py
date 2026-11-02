"""Tests for flight_blender.geo_fence/tasks.py – Celery tasks with mocked HTTP."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from flight_blender.tasks.geo_fence_task import download_geozone_source, write_geo_zone


# ---------------------------------------------------------------------------
# write_geo_zone task
# ---------------------------------------------------------------------------


class TestWriteGeoZoneTask:
    def _minimal_geo_zone(self, title="Test Zone", description="Test"):
        """Build a minimal geo_zone dict that GeoZoneParser can handle."""
        return {
            "title": title,
            "description": description,
            "features": [
                {
                    "name": "TestFeature",
                    "geometry": [
                        {
                            "horizontalProjection": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [0.0, 0.0],
                                        [1.0, 0.0],
                                        [1.0, 1.0],
                                        [0.0, 1.0],
                                        [0.0, 0.0],
                                    ]
                                ],
                            }
                        }
                    ],
                }
            ],
        }

    def _make_geo_zone_feature(self):
        """Return a dict-like object that behaves like a GeoZoneFeature."""

        class FakeGeoZoneFeature(dict):
            """Wraps a plain dict so it also exposes .name and .geometry attributes."""

            @property
            def name(self):
                return self["name"]

            @property
            def geometry(self):
                return self["_geometry"]

        geom_dict = {
            "horizontalProjection": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
            }
        }
        return FakeGeoZoneFeature({"name": "TestFeature", "_geometry": [geom_dict]})

    def test_write_geo_zone_saves_geofence(self):
        geo_zone_dict = self._minimal_geo_zone()
        feature = self._make_geo_zone_feature()

        mock_parse_response = MagicMock()
        mock_parse_response.feature_list = [feature]

        with patch("flight_blender.tasks.geo_fence_task.GeoZoneParser") as mock_parser_cls:
            mock_parser = MagicMock()
            mock_parser.parse_validate_geozone.return_value = mock_parse_response
            mock_parser_cls.return_value = mock_parser

            write_geo_zone(geo_zone=json.dumps(geo_zone_dict), test_harness_datasource="0")

    def test_write_geo_zone_test_harness_datasource(self):
        geo_zone_dict = self._minimal_geo_zone()
        feature = self._make_geo_zone_feature()

        mock_parse_response = MagicMock()
        mock_parse_response.feature_list = [feature]

        with patch("flight_blender.tasks.geo_fence_task.GeoZoneParser") as mock_parser_cls:
            mock_parser = MagicMock()
            mock_parser.parse_validate_geozone.return_value = mock_parse_response
            mock_parser_cls.return_value = mock_parser

            # Should not raise when test_harness_datasource="1"
            write_geo_zone(geo_zone=json.dumps(geo_zone_dict), test_harness_datasource="1")


# ---------------------------------------------------------------------------
# download_geozone_source task
# ---------------------------------------------------------------------------


class TestDownloadGeozoneSourceTask:
    @staticmethod
    def _mock_http_client(response=None, side_effect=None):
        client = AsyncMock()
        client.get.return_value = response
        client.get.side_effect = side_effect
        client_context = MagicMock()
        client_context.return_value.__aenter__ = AsyncMock(return_value=client)
        client_context.return_value.__aexit__ = AsyncMock(return_value=None)
        return client_context

    def test_successful_download_queues_write_task(self):
        geo_zone_data = {"title": "Test", "description": "Desc", "features": []}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = geo_zone_data

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = True

        with (
            patch("flight_blender.tasks.geo_fence_task.httpx.AsyncClient", self._mock_http_client(response=mock_response)),
            patch("flight_blender.tasks.geo_fence_task.get_async_redis", return_value=mock_redis),
            patch("flight_blender.tasks.geo_fence_task.write_geo_zone.delay") as mock_delay,
        ):
            download_geozone_source("http://example.com/zones.json", "test-source-123")
            mock_delay.assert_called_once()

    def test_non_200_response_stores_unsupported(self):
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = True

        with (
            patch("flight_blender.tasks.geo_fence_task.httpx.AsyncClient", self._mock_http_client(response=mock_response)),
            patch("flight_blender.tasks.geo_fence_task.get_async_redis", return_value=mock_redis),
        ):
            download_geozone_source("http://example.com/zones.json", "test-source-404")
            mock_redis.set.assert_called_once()
            stored = json.loads(mock_redis.set.call_args[0][1])
            assert stored["result"] == "Unsupported"

    def test_connection_error_stores_error(self):
        mock_redis = AsyncMock()
        mock_redis.exists.return_value = True

        with (
            patch(
                "flight_blender.tasks.geo_fence_task.httpx.AsyncClient",
                self._mock_http_client(side_effect=httpx.ConnectError("timeout")),
            ),
            patch("flight_blender.tasks.geo_fence_task.get_async_redis", return_value=mock_redis),
        ):
            download_geozone_source("http://unreachable.invalid/zones.json", "test-source-err")
            mock_redis.set.assert_called_once()
            stored = json.loads(mock_redis.set.call_args[0][1])
            assert stored["result"] == "Error"

    def test_redis_key_not_exists_skips_set(self):
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = False

        with (
            patch("flight_blender.tasks.geo_fence_task.httpx.AsyncClient", self._mock_http_client(response=mock_response)),
            patch("flight_blender.tasks.geo_fence_task.get_async_redis", return_value=mock_redis),
        ):
            download_geozone_source("http://example.com/zones.json", "test-source-no-key")
            mock_redis.set.assert_not_called()
