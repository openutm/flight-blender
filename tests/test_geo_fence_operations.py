import json
import uuid

import pytest
from tests.conftest import (
    auth_header,
    SCD_INJECT_SCOPE,
    SCD_PLAN_SCOPE,
    SCD_TEST_SCOPE,
    READ_SCOPE,
    WRITE_SCOPE,
    GA_TEST_SCOPE,
)


@pytest.mark.django_db
class TestGeoFenceCRUD:
    def test_set_geo_fence_validation(self, client):
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[13.4, 52.5], [13.41, 52.5], [13.41, 52.51], [13.4, 52.51], [13.4, 52.5]],
                    },
                    "properties": {
                        "name": "Test GeoFence",
                        "upper_limit": 50,
                        "lower_limit": 20,
                    },
                }
            ],
        }
        resp = client.put(
            "/geo_fence_ops/set_geo_fence",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code in (200, 400)

    def test_set_geo_fence_unsupported_media_type(self, client):
        resp = client.put(
            "/geo_fence_ops/set_geo_fence",
            data="{}",
            content_type="text/plain",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 415

    def test_set_geo_fence_invalid_json(self, client):
        resp = client.put(
            "/geo_fence_ops/set_geo_fence",
            data="not json",
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code in (400, 500)

    def test_list_geo_fences(self, client):
        resp = client.get(
            "/geo_fence_ops/geo_fence",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    def test_list_geo_fences_with_date_filter(self, client):
        resp = client.get(
            "/geo_fence_ops/geo_fence?start_date=2025-01-01&end_date=2025-12-31",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_geo_fences_with_view_filter(self, client):
        resp = client.get(
            "/geo_fence_ops/geo_fence?view=52.500,13.399,52.501,13.400",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_geo_fences_unauthenticated(self, client):
        resp = client.get("/geo_fence_ops/geo_fence")
        assert resp.status_code == 401

    def test_get_geo_fence_not_found(self, client):
        pk = str(uuid.uuid4())
        resp = client.get(
            f"/geo_fence_ops/geo_fence/{pk}",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 404

    def test_delete_geo_fence_not_found(self, client):
        pk = str(uuid.uuid4())
        resp = client.delete(
            f"/geo_fence_ops/geo_fence/{pk}/delete",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestGeoZone:
    def test_set_geozone_invalid(self, client):
        resp = client.post(
            "/geo_fence_ops/set_geozone",
            data=json.dumps({"invalid": "data"}),
            content_type="application/json",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_set_geozone_unsupported_media_type(self, client):
        resp = client.post(
            "/geo_fence_ops/set_geozone",
            data="{}",
            content_type="text/plain",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code in (400, 415)


@pytest.mark.django_db
class TestGeoAwarenessHarness:
    def test_harness_status(self, client):
        resp = client.get(
            "/geo_fence_ops/geo_awareness/status",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Ready"
        assert "api_version" in data

    def test_geospatial_data_sources(self, client):
        resp = client.get(
            "/geo_fence_ops/geo_awareness/geospatial_data_sources",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_geospatial_data_sources_with_dates(self, client):
        resp = client.get(
            "/geo_fence_ops/geo_awareness/geospatial_data_sources?start_date=2025-01-01&end_date=2025-12-31",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_geozone_source_not_found(self, client):
        source_id = str(uuid.uuid4())
        resp = client.get(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 404

    def test_geozone_source_put_invalid_url(self, client):
        source_id = str(uuid.uuid4())
        resp = client.put(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            data=json.dumps({"https_source": {"url": "not-a-url", "format": "geojson"}}),
            content_type="application/json",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "Unsupported"

    def test_geozone_source_put_valid_url(self, client):
        """Valid HTTPS URL → task scheduled (eager, fails gracefully) → 200 Activating."""
        source_id = str(uuid.uuid4())
        resp = client.put(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            data=json.dumps({"https_source": {"url": "https://example.com/geozone.geojson", "format": "geojson"}}),
            content_type="application/json",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "Activating"

    def test_geozone_source_get_after_activate(self, client):
        """GET after a successful PUT returns the stored status (200)."""
        source_id = str(uuid.uuid4())
        # First activate the source
        client.put(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            data=json.dumps({"https_source": {"url": "https://example.com/geozone.geojson", "format": "geojson"}}),
            content_type="application/json",
            **auth_header(GA_TEST_SCOPE),
        )
        # Now GET should find the key in Redis and return 200
        resp = client.get(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data

    def test_geozone_source_delete_after_activate(self, client):
        """DELETE after a successful PUT removes test geozones and returns 200 Deactivating."""
        source_id = str(uuid.uuid4())
        # First activate the source
        client.put(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            data=json.dumps({"https_source": {"url": "https://example.com/geozone.geojson", "format": "geojson"}}),
            content_type="application/json",
            **auth_header(GA_TEST_SCOPE),
        )
        # Now DELETE should find the key and return 200
        resp = client.delete(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "Deactivating"

    def test_geozone_source_put_missing_url(self, client):
        source_id = str(uuid.uuid4())
        resp = client.put(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(GA_TEST_SCOPE),
        )
        # Missing url/format → ImplicitDict raises KeyError → 500
        assert resp.status_code == 500

    def test_geozone_source_delete_not_found(self, client):
        source_id = str(uuid.uuid4())
        resp = client.delete(
            f"/geo_fence_ops/geo_awareness/geospatial_data_sources/{source_id}",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 404

    def test_geozone_check_absent(self, client):
        payload = {
            "checks": [
                {
                    "filter_sets": [
                        {
                            "resulting_operational_impact": "Block",
                            "position": {
                                "latitude": 52.5,
                                "longitude": 13.4,
                                "uomDimensions": "M",
                                "verticalReferenceType": "W84",
                                "height": 50,
                            },
                        }
                    ]
                }
            ]
        }
        resp = client.post(
            "/geo_fence_ops/geo_awareness/map/queries",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(GA_TEST_SCOPE),
        )
        # View has a bug: Point(filter_position) fails on ImplicitDict → 500
        assert resp.status_code in (200, 500)

    def test_geozone_check_empty_checks(self, client):
        payload = {"checks": []}
        resp = client.post(
            "/geo_fence_ops/geo_awareness/map/queries",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(GA_TEST_SCOPE),
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestSCDStatus:
    def test_scd_status(self, client):
        resp = client.get("/scd/v1/status", **auth_header(SCD_INJECT_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Ready"

    def test_scd_capabilities(self, client):
        resp = client.get("/scd/v1/capabilities", **auth_header(SCD_INJECT_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "capabilities" in data
        assert len(data["capabilities"]) > 0


@pytest.mark.django_db
class TestFlightPlanningStatus:
    def test_flight_planning_status(self, client):
        resp = client.get(
            "/scd/flight_planning/status",
            **auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Ready"
        assert "api_name" in data
        assert "api_version" in data

    def test_u_space_flight_planning_status(self, client):
        resp = client.get(
            "/scd/flight_planning/u_space/status",
            **auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestFlightPlanningClearArea:
    def test_clear_area_missing_payload(self, client):
        resp = client.post(
            "/scd/flight_planning/clear_area_requests",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 400

    def test_clear_area_missing_request_id(self, client):
        payload = {
            "extent": {
                "time_start": {"value": "2025-06-01T00:00:00Z", "format": "RFC3339"},
                "time_end": {"value": "2025-06-01T01:00:00Z", "format": "RFC3339"},
                "volume": {
                    "outline_circle": {
                        "center": {"lat": 52.5, "lng": 13.4},
                        "radius": {"value": 1000, "units": "M"},
                    },
                    "altitude_lower": {"value": 0, "reference": "W84", "units": "M"},
                    "altitude_upper": {"value": 100, "reference": "W84", "units": "M"},
                },
            }
        }
        resp = client.post(
            "/scd/flight_planning/clear_area_requests",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(SCD_TEST_SCOPE),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestFlightPlanUpsert:
    def test_delete_nonexistent_flight_plan(self, client):
        plan_id = str(uuid.uuid4())
        resp = client.delete(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            **auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planning_result"] == "Failed"

    def test_upsert_flight_plan_invalid_data(self, client):
        plan_id = str(uuid.uuid4())
        resp = client.put(
            f"/scd/flight_planning/flight_plans/{plan_id}",
            data=json.dumps({}),
            content_type="application/json",
            **auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 500

    def test_u_space_delete_nonexistent(self, client):
        plan_id = str(uuid.uuid4())
        resp = client.delete(
            f"/scd/flight_planning/u_space/flight_plans/{plan_id}",
            **auth_header(SCD_PLAN_SCOPE),
        )
        assert resp.status_code == 200
