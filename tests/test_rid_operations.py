import json
import uuid
from unittest.mock import MagicMock, patch

import arrow
import pytest
from tests.conftest import fastapi_auth_header, READ_SCOPE, WRITE_SCOPE, DSS_READ_SCOPE, DSS_WRITE_SCOPE, RID_INJECT_SCOPE


@pytest.mark.django_db
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


@pytest.mark.django_db
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
        with patch("flight_blender.rid.views.SubscriptionsHelper.check_subscription_exists", return_value=True):
            with patch(
                "flight_blender.common.database_operations.FlightBlenderDatabaseReader.get_active_rid_observations_for_view",
                return_value=[],
            ):
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
        with patch("flight_blender.rid.views.SubscriptionsHelper.check_subscription_exists", return_value=False):
            with patch(
                "flight_blender.rid.views.SubscriptionsHelper.create_new_rid_subscription",
                return_value=mock_sub,
            ) as mock_create:
                with patch("flight_blender.api.routers.rid.run_ussp_polling_for_rid") as mock_poll:
                    with patch(
                        "flight_blender.common.database_operations.FlightBlenderDatabaseReader.get_active_rid_observations_for_view",
                        return_value=[],
                    ):
                        resp = mounted_sync_client.get(
                            "/rid/display_data?view=52.500,13.399,52.501,13.400",
                            headers=fastapi_auth_header(DSS_READ_SCOPE),
                        )
        assert resp.status_code == 200
        mock_create.assert_called_once()
        mock_poll.delay.assert_called_once()


@pytest.mark.django_db
class TestRIDFlightData:
    def test_get_flight_data_not_found(self, mounted_sync_client):
        flight_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/rid/display_data/{flight_id}",
            headers=fastapi_auth_header(DSS_READ_SCOPE),
        )
        assert resp.status_code == 404

    def test_get_flight_data_response_excludes_null_fields(self, mounted_sync_client):
        from flight_blender.rid.models import RIDFlightDetail

        flight_id = uuid.uuid4()
        RIDFlightDetail.objects.create(
            id=flight_id,
            operator_id="test-operator",
            operation_description="Test flight",
            operator_location=None,
            auth_data=None,
            uas_id=None,
            eu_classification=None,
        )
        resp = mounted_sync_client.get(
            f"/rid/display_data/{flight_id}",
            headers=fastapi_auth_header(DSS_READ_SCOPE),
        )
        assert resp.status_code == 200
        details = resp.json()["details"]
        for key, value in details.items():
            assert value is not None, f"Field '{key}' should be absent when null, not serialised as null"


@pytest.mark.django_db
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
            "/rid/create_dss_subscription?view=52.5,13.4,52.4,13.3",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
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


@pytest.mark.django_db
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


@pytest.mark.django_db
class TestRIDGetRIDData:
    def test_get_rid_data_not_found(self, mounted_sync_client):
        sub_id = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/rid/get_rid_data/{sub_id}",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 404


@pytest.mark.django_db
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

    def test_isa_callback_updates_subscription_in_db(self, mounted_sync_client):
        from flight_blender.rid.models import ISASubscription

        isa_id = str(uuid.uuid4())
        subscription_id = str(uuid.uuid4())
        flight_details = json.dumps({
            "service_areas": [
                {
                    "id": isa_id,
                    "uss_base_url": "https://example.com/old",
                    "owner": "test_owner",
                    "time_start": {"value": "2025-06-01T00:00:00Z", "format": "RFC3339"},
                    "time_end": {"value": "2025-06-01T01:00:00Z", "format": "RFC3339"},
                    "version": "1",
                }
            ],
            "subscription": {
                "id": str(uuid.uuid4()),
                "uss_base_url": "https://example.com",
                "owner": "test_owner",
                "notification_index": 0,
                "time_start": {"value": "2025-06-01T00:00:00Z", "format": "RFC3339"},
                "time_end": {"value": "2025-06-01T01:00:00Z", "format": "RFC3339"},
                "version": "1",
            },
        })
        record = ISASubscription.objects.create(
            subscription_id=subscription_id,
            view="52.5,13.4,52.6,13.5",
            view_hash=12345,
            end_datetime=arrow.now().shift(hours=1).datetime,
            flight_details=flight_details,
            is_simulated=False,
        )

        payload = {
            "subscriptions": [{"subscription_id": subscription_id, "notification_index": 0}],
            "service_area": {
                "id": isa_id,
                "uss_base_url": "https://example.com/updated",
                "owner": "test_owner",
                "time_start": {"value": "2025-06-01T00:00:00Z", "format": "RFC3339"},
                "time_end": {"value": "2025-06-01T01:00:00Z", "format": "RFC3339"},
                "version": "2",
            },
        }
        resp = mounted_sync_client.post(
            f"/rid/uss/identification_service_areas/{isa_id}",
            content=json.dumps(payload),
            headers={**fastapi_auth_header(DSS_WRITE_SCOPE), "Content-Type": "application/json"},
        )
        assert resp.status_code == 204

        record.refresh_from_db()
        updated_sa = json.loads(record.flight_details)["service_areas"][0]
        assert updated_sa["uss_base_url"] == "https://example.com/updated"
        assert updated_sa["version"] == "2"
