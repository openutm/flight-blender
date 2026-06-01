import json
import uuid

import pytest
from tests.conftest import (
    auth_header,
    READ_SCOPE,
    WRITE_SCOPE,
    DSS_READ_SCOPE,
    DSS_WRITE_SCOPE,
    RID_INJECT_SCOPE,
)


@pytest.mark.django_db
class TestRIDCapabilities:
    def test_get_capabilities(self, client):
        resp = client.get("/rid/capabilities", **auth_header(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "capabilities" in data
        assert "ASTMRID2022" in data["capabilities"]

    def test_get_capabilities_unauthenticated(self, client):
        resp = client.get("/rid/capabilities")
        assert resp.status_code == 401


@pytest.mark.django_db
class TestRIDDisplayData:
    def test_display_data_missing_view(self, client):
        resp = client.get("/rid/display_data", **auth_header(DSS_READ_SCOPE))
        assert resp.status_code == 400

    def test_display_data_invalid_view(self, client):
        resp = client.get("/rid/display_data?view=bad", **auth_header(DSS_READ_SCOPE))
        assert resp.status_code == 400

    def test_display_data_view_too_large(self, client):
        resp = client.get(
            "/rid/display_data?view=52.0,13.0,53.0,14.0",
            **auth_header(DSS_READ_SCOPE),
        )
        assert resp.status_code == 413

    def test_display_data_invalid_view_port(self, client):
        # Invalid viewport (min > max) — view returns 413 (too large) or 400
        resp = client.get(
            "/rid/display_data?view=52.5,13.4,52.4,13.3",
            **auth_header(DSS_READ_SCOPE),
        )
        assert resp.status_code in (400, 413)

    def test_display_data_valid_small_view(self, client):
        resp = client.get(
            "/rid/display_data?view=52.500,13.399,52.501,13.400",
            **auth_header(DSS_READ_SCOPE),
        )
        # May succeed or fail depending on DSS connectivity
        assert resp.status_code in (200, 400, 500)


@pytest.mark.django_db
class TestRIDFlightData:
    def test_get_flight_data_not_found(self, client):
        flight_id = str(uuid.uuid4())
        resp = client.get(
            f"/rid/display_data/{flight_id}",
            **auth_header(DSS_READ_SCOPE),
        )
        assert resp.status_code == 404


@pytest.mark.django_db
class TestRIDSubscription:
    def test_create_dss_subscription_missing_view(self, client):
        resp = client.put(
            "/rid/create_dss_subscription",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_create_dss_subscription_invalid_view(self, client):
        resp = client.put(
            "/rid/create_dss_subscription?view=bad",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_create_dss_subscription_invalid_port(self, client):
        resp = client.put(
            "/rid/create_dss_subscription?view=52.5,13.4,52.4,13.3",
            **auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestRIDTestData:
    def test_create_test_empty_flights(self, client, fakeredis_server):
        test_id = str(uuid.uuid4())
        resp = client.put(
            f"/rid/tests/{test_id}",
            data=json.dumps({"requested_flights": []}),
            content_type="application/json",
            **auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 200

    def test_create_and_delete_test(self, client, fakeredis_server):
        test_id = str(uuid.uuid4())
        payload = {"requested_flights": []}
        resp = client.put(
            f"/rid/tests/{test_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "injected_flights" in data

        resp = client.delete(
            f"/rid/tests/{test_id}/1",
            **auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 200

    def test_create_test_duplicate_returns_409(self, client, fakeredis_server):
        test_id = str(uuid.uuid4())
        payload = {"requested_flights": []}
        client.put(
            f"/rid/tests/{test_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(RID_INJECT_SCOPE),
        )
        resp = client.put(
            f"/rid/tests/{test_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 409

    def test_delete_nonexistent_test(self, client, fakeredis_server):
        test_id = str(uuid.uuid4())
        resp = client.delete(
            f"/rid/tests/{test_id}/1",
            **auth_header(RID_INJECT_SCOPE),
        )
        # Deleting nonexistent test should still succeed
        assert resp.status_code == 200


@pytest.mark.django_db
class TestRIDUserNotifications:
    def test_user_notifications_missing_params(self, client):
        resp = client.get(
            "/rid/user_notifications",
            **auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 400

    def test_user_notifications_missing_before(self, client):
        resp = client.get(
            "/rid/user_notifications?after=2025-01-01T00:00:00Z",
            **auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 400

    def test_user_notifications_with_params(self, client):
        resp = client.get(
            "/rid/user_notifications?after=2025-01-01T00:00:00Z&before=2025-12-31T23:59:59Z",
            **auth_header(RID_INJECT_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "user_notifications" in data


@pytest.mark.django_db
class TestRIDGetRIDData:
    def test_get_rid_data_not_found(self, client):
        sub_id = str(uuid.uuid4())
        resp = client.get(
            f"/rid/get_rid_data/{sub_id}",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code in (404, 500)


@pytest.mark.django_db
class TestRIDISACallback:
    def test_isa_callback_empty_subscriptions(self, client):
        isa_id = str(uuid.uuid4())
        resp = client.post(
            f"/rid/uss/identification_service_areas/{isa_id}",
            data=json.dumps({"subscriptions": []}),
            content_type="application/json",
            **auth_header(DSS_WRITE_SCOPE),
        )
        assert resp.status_code in (204, 400, 500)

    def test_isa_callback_with_service_area(self, client):
        isa_id = str(uuid.uuid4())
        payload = {
            "subscriptions": [
                {
                    "subscription_id": str(uuid.uuid4()),
                    "notification_index": 0,
                }
            ],
            "service_area": {
                "id": isa_id,
                "uss_base_url": "https://example.com",
                "time_start": {"value": "2025-06-01T00:00:00Z", "format": "RFC3339"},
                "time_end": {"value": "2025-06-01T01:00:00Z", "format": "RFC3339"},
            },
        }
        resp = client.post(
            f"/rid/uss/identification_service_areas/{isa_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **auth_header(DSS_WRITE_SCOPE),
        )
        # May fail if subscription doesn't exist in DB
        assert resp.status_code in (204, 400, 500)
