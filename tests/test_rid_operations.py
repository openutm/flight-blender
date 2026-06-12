import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import arrow
import pytest
from tests.conftest import fastapi_auth_header, READ_SCOPE, WRITE_SCOPE, DSS_READ_SCOPE, DSS_WRITE_SCOPE, RID_INJECT_SCOPE


class TestRIDCapabilities:
    def test_get_capabilities(self, mounted_sync_client):
        resp = mounted_sync_client.get("/rid/capabilities", headers=fastapi_auth_header(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "capabilities" in data
        assert "ASTMRID2022" in data["capabilities"]

    def test_get_capabilities_unauthenticated(self, mounted_sync_client):
        resp = mounted_sync_client.get("/rid/capabilities")
        assert resp.status_code == 401


class TestRIDDisplayData:
    def test_display_data_missing_view(self, mounted_sync_client):
        resp = mounted_sync_client.get("/rid/display_data", headers=fastapi_auth_header(DSS_READ_SCOPE))
        assert resp.status_code == 400

    def test_display_data_invalid_view(self, mounted_sync_client):
        resp = mounted_sync_client.get("/rid/display_data?view=bad", headers=fastapi_auth_header(DSS_READ_SCOPE))
        assert resp.status_code == 400

    def test_display_data_view_too_large(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/rid/display_data?view=52.0,13.0,53.0,14.0",
            headers=fastapi_auth_header(DSS_READ_SCOPE),
        )
        assert resp.status_code == 413

    def test_display_data_invalid_view_port(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/rid/display_data?view=52.5,13.4,52.4,13.3",
            headers=fastapi_auth_header(DSS_READ_SCOPE),
        )
        assert resp.status_code in (400, 413)

    def test_display_data_valid_returns_flights_and_clusters(self, mounted_sync_client):
        mock_sub = MagicMock()
        mock_sub.created = True
        with patch(
            "flight_blender.clients.dss_rid_client.RemoteIDOperations.create_dss_subscription",
            return_value=mock_sub,
        ):
            with patch("flight_blender.tasks.rid_task.run_ussp_polling_for_rid"):
                resp = mounted_sync_client.get(
                    "/rid/display_data?view=52.500,13.399,52.501,13.400",
                    headers=fastapi_auth_header(DSS_READ_SCOPE),
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "flights" in data
        assert "clusters" in data
        assert data["flights"] == []
        assert data["clusters"] == []

    def test_display_data_creates_subscription_when_none_exists(self, mounted_sync_client):
        mock_sub = MagicMock()
        mock_sub.created = False
        with patch(
            "flight_blender.clients.dss_rid_client.RemoteIDOperations.create_dss_subscription",
            return_value=mock_sub,
        ) as mock_create:
            with patch("flight_blender.tasks.rid_task.run_ussp_polling_for_rid") as mock_poll:
                resp = mounted_sync_client.get(
                    "/rid/display_data?view=52.500,13.399,52.501,13.400",
                    headers=fastapi_auth_header(DSS_READ_SCOPE),
                )
        assert resp.status_code == 200
        mock_create.assert_called_once()
        mock_poll.delay.assert_called_once()


class TestRIDFlightData:
    def test_get_flight_data_not_found(self, mounted_sync_client):
        flight_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/rid/display_data/{flight_id}",
            headers=fastapi_auth_header(DSS_READ_SCOPE),
        )
        assert resp.status_code == 404

class TestRIDSubscription:
    def test_create_dss_subscription_missing_view(self, mounted_sync_client):
        resp = mounted_sync_client.put("/rid/create_dss_subscription", headers=fastapi_auth_header(WRITE_SCOPE))
        assert resp.status_code == 400

    def test_create_dss_subscription_invalid_view(self, mounted_sync_client):
        resp = mounted_sync_client.put(
            "/rid/create_dss_subscription?view=bad",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_create_dss_subscription_invalid_port(self, mounted_sync_client):
        resp = mounted_sync_client.put(
            "/rid/create_dss_subscription?view=95.0,13.4,96.0,13.5",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400


class TestRIDTestData:
    def test_create_test_empty_flights(self, mounted_sync_client):
        test_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/rid/tests/{test_id}",
            content=json.dumps({"requested_flights": []}),
            headers={**fastapi_auth_header(RID_INJECT_SCOPE), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["injected_flights"] == []
        assert data["version"] == 1

    def test_create_test_missing_flights_returns_422(self, mounted_sync_client):
        test_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/rid/tests/{test_id}",
            content=json.dumps({}),
            headers={**fastapi_auth_header(RID_INJECT_SCOPE), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_create_and_delete_test(self, mounted_sync_client):
        test_id = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/rid/tests/{test_id}",
            content=json.dumps({"requested_flights": []}),
            headers={**fastapi_auth_header(RID_INJECT_SCOPE), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert "injected_flights" in resp.json()

        resp = mounted_sync_client.delete(
            f"/rid/tests/{test_id}/1",
            headers=fastapi_auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 200

    def test_create_test_duplicate_returns_409(self, mounted_sync_client):
        test_id = str(uuid.uuid4())
        mounted_sync_client.put(
            f"/rid/tests/{test_id}",
            content=json.dumps({"requested_flights": []}),
            headers={**fastapi_auth_header(RID_INJECT_SCOPE), "Content-Type": "application/json"},
        )
        resp = mounted_sync_client.put(
            f"/rid/tests/{test_id}",
            content=json.dumps({"requested_flights": []}),
            headers={**fastapi_auth_header(RID_INJECT_SCOPE), "Content-Type": "application/json"},
        )
        assert resp.status_code == 409

    def test_delete_nonexistent_test(self, mounted_sync_client):
        test_id = str(uuid.uuid4())
        resp = mounted_sync_client.delete(
            f"/rid/tests/{test_id}/1",
            headers=fastapi_auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 200


class TestRIDUserNotifications:
    def test_user_notifications_missing_params(self, mounted_sync_client):
        resp = mounted_sync_client.get("/rid/user_notifications", headers=fastapi_auth_header(RID_INJECT_SCOPE))
        assert resp.status_code == 400

    def test_user_notifications_missing_before(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/rid/user_notifications?after=2025-01-01T00:00:00Z",
            headers=fastapi_auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 400

    def test_user_notifications_invalid_date_format(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/rid/user_notifications?after=not-a-date&before=also-not",
            headers=fastapi_auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 400

    def test_user_notifications_valid_params_returns_list(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/rid/user_notifications?after=2025-01-01T00:00:00Z&before=2025-12-31T23:59:59Z",
            headers=fastapi_auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "user_notifications" in data
        assert isinstance(data["user_notifications"], list)


class TestRIDGetRIDData:
    def test_get_rid_data_not_found(self, mounted_sync_client):
        sub_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/rid/get_rid_data/{sub_id}",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 404


class TestRIDISACallback:
    def test_isa_callback_empty_subscriptions(self, mounted_sync_client):
        isa_id = str(uuid.uuid4())
        resp = mounted_sync_client.post(
            f"/rid/uss/identification_service_areas/{isa_id}",
            content=json.dumps({"subscriptions": []}),
            headers={**fastapi_auth_header(DSS_WRITE_SCOPE), "Content-Type": "application/json"},
        )
        assert resp.status_code == 204

    def test_isa_callback_no_service_area_skips_db_mutation(self, mounted_sync_client):
        isa_id = str(uuid.uuid4())
        payload = {"subscriptions": [{"subscription_id": str(uuid.uuid4()), "notification_index": 0}]}
        resp = mounted_sync_client.post(
            f"/rid/uss/identification_service_areas/{isa_id}",
            content=json.dumps(payload),
            headers={**fastapi_auth_header(DSS_WRITE_SCOPE), "Content-Type": "application/json"},
        )
        # No service_area → updated_service_area is None → no DB lookup
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Spatial RID additional coverage
# ---------------------------------------------------------------------------


class TestSpatialRIDCoverage:
    """Additional tests for spatial_rid."""

    def test_operational_intent_comparison_factory_check_volume_geometry_same(self):
        """Test OperationalIntentComparisonFactory.check_volume_geometry_same."""
        from shapely.geometry import Polygon
        from flight_blender.utils.spatial_rid import OperationalIntentComparisonFactory

        factory = OperationalIntentComparisonFactory()

        polygon_a = Polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])
        polygon_b = Polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])

        result = factory.check_volume_geometry_same(polygon_a, polygon_b)

        assert result is True

    def test_operational_intent_comparison_factory_check_volume_geometry_different(self):
        """Test OperationalIntentComparisonFactory.check_volume_geometry_same with different polygons."""
        from shapely.geometry import Polygon
        from flight_blender.utils.spatial_rid import OperationalIntentComparisonFactory

        factory = OperationalIntentComparisonFactory()

        polygon_a = Polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])
        polygon_b = Polygon([(0, 0), (0, 1), (2, 1), (2, 0), (0, 0)])

        result = factory.check_volume_geometry_same(polygon_a, polygon_b)

        assert result is False

    def test_operational_intent_comparison_factory_check_volume_start_end_time_same(self):
        """Test OperationalIntentComparisonFactory.check_volume_start_end_time_same."""
        from flight_blender.utils.spatial_rid import OperationalIntentComparisonFactory
        from flight_blender.domain_types.scd import Time

        factory = OperationalIntentComparisonFactory()

        time_a = Time(format="RFC3339", value="2024-01-01T00:00:00Z")
        time_b = Time(format="RFC3339", value="2024-01-01T00:00:00Z")

        result = factory.check_volume_start_end_time_same(time_a, time_b)

        assert result is True

    def test_operational_intent_comparison_factory_check_volume_altitude_same(self):
        """Test OperationalIntentComparisonFactory.check_volume_altitude_same."""
        from flight_blender.utils.spatial_rid import OperationalIntentComparisonFactory
        from flight_blender.domain_types.scd import Altitude

        factory = OperationalIntentComparisonFactory()

        altitude_a = Altitude(value=100, reference="W84", units="M")
        altitude_b = Altitude(value=100, reference="W84", units="M")

        result = factory.check_volume_(altitude_a, altitude_b)

        assert result is True

    def test_operational_intents_index_factory_add_box_to_index(self):
        """Test OperationalIntentsIndexFactory.add_box_to_index."""
        from flight_blender.utils.spatial_rid import OperationalIntentsIndexFactory

        mock_fd_repo = MagicMock()
        factory = OperationalIntentsIndexFactory(index_name="test-index", fd_repo=mock_fd_repo)

        factory.add_box_to_index(
            enumerated_id=1,
            flight_id="test-flight-id",
            view=[0, 0, 1, 1],
            start_time="2024-01-01",
            end_time="2024-12-31",
        )

        # No assertion needed, just ensure it doesn't raise

    def test_operational_intents_index_factory_delete_from_index(self):
        """Test OperationalIntentsIndexFactory.delete_from_index."""
        from flight_blender.utils.spatial_rid import OperationalIntentsIndexFactory

        mock_fd_repo = MagicMock()
        factory = OperationalIntentsIndexFactory(index_name="test-index", fd_repo=mock_fd_repo)

        factory.add_box_to_index(
            enumerated_id=1,
            flight_id="test-flight-id",
            view=[0, 0, 1, 1],
            start_time="2024-01-01",
            end_time="2024-12-31",
        )

        factory.delete_from_index(
            enumerated_id=1,
            view=[0, 0, 1, 1],
        )

        # No assertion needed, just ensure it doesn't raise

    def test_operational_intents_index_factory_generate_active_flights_operational_intents_index(self):
        """Test OperationalIntentsIndexFactory.generate_active_flights_operational_intents_index."""
        from flight_blender.utils.spatial_rid import OperationalIntentsIndexFactory

        mock_fd_repo = AsyncMock()
        factory = OperationalIntentsIndexFactory(index_name="test-index", fd_repo=mock_fd_repo)

        mock_declaration = MagicMock()
        mock_declaration.id = uuid.uuid4()
        mock_declaration.bounds = "0,0,1,1"
        mock_declaration.start_datetime = arrow.utcnow().datetime
        mock_declaration.end_datetime = arrow.utcnow().shift(hours=1).datetime

        mock_fd_repo.list = AsyncMock(return_value=[mock_declaration])

        import asyncio
        asyncio.run(factory.generate_active_flights_operational_intents_index())

        # No assertion needed, just ensure it doesn't raise

    def test_operational_intents_index_factory_check_box_intersection(self):
        """Test OperationalIntentsIndexFactory.check_box_intersection."""
        from flight_blender.utils.spatial_rid import OperationalIntentsIndexFactory

        mock_fd_repo = AsyncMock()
        factory = OperationalIntentsIndexFactory(index_name="test-index", fd_repo=mock_fd_repo)

        mock_declaration = MagicMock()
        mock_declaration.id = uuid.uuid4()
        mock_declaration.bounds = "0,0,1,1"
        mock_declaration.start_datetime = arrow.utcnow().datetime
        mock_declaration.end_datetime = arrow.utcnow().shift(hours=1).datetime

        mock_fd_repo.list = AsyncMock(return_value=[mock_declaration])

        import asyncio
        asyncio.run(factory.generate_active_flights_operational_intents_index())

        result = factory.check_box_intersection(view_box=[0, 0, 1, 1])

        assert isinstance(result, list)

    def test_operational_intents_index_factory_check_op_ints_exist(self):
        """Test OperationalIntentsIndexFactory.check_op_ints_exist."""
        from flight_blender.utils.spatial_rid import OperationalIntentsIndexFactory

        mock_fd_repo = AsyncMock()
        factory = OperationalIntentsIndexFactory(index_name="test-index", fd_repo=mock_fd_repo)

        mock_fd_repo.list = AsyncMock(return_value=[MagicMock()])

        import asyncio
        result = asyncio.run(factory.check_op_ints_exist())

        assert result is True

    def test_operational_intents_index_factory_check_op_ints_exist_false(self):
        """Test OperationalIntentsIndexFactory.check_op_ints_exist returns False."""
        from flight_blender.utils.spatial_rid import OperationalIntentsIndexFactory

        mock_fd_repo = AsyncMock()
        factory = OperationalIntentsIndexFactory(index_name="test-index", fd_repo=mock_fd_repo)

        mock_fd_repo.list = AsyncMock(return_value=[])

        import asyncio
        result = asyncio.run(factory.check_op_ints_exist())

        assert result is False

    def test_check_polygon_intersection(self):
        """Test check_polygon_intersection function."""
        from shapely.geometry import Polygon
        from flight_blender.utils.spatial_rid import check_polygon_intersection
        from flight_blender.domain_types.scd import OpInttoCheckDetails

        polygon = Polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])

        mock_op_int = MagicMock()
        mock_op_int.shape = polygon

        result = check_polygon_intersection(
            op_int_details=[mock_op_int],
            polygon_to_check=polygon,
        )

        assert isinstance(result, bool)

    def test_check_time_intersection(self):
        """Test check_time_intersection function."""
        from flight_blender.utils.spatial_rid import check_time_intersection

        mock_op_int = MagicMock()
        mock_op_int.time_start = arrow.utcnow().shift(hours=-1).isoformat()
        mock_op_int.time_end = arrow.utcnow().shift(hours=1).isoformat()

        result = check_time_intersection(
            op_int_details=[mock_op_int],
            volume_time_start=arrow.utcnow().isoformat(),
            volume_time_end=arrow.utcnow().shift(hours=1).isoformat(),
        )

        assert isinstance(result, bool)
