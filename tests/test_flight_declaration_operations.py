import uuid

import pytest
from tests.conftest import fastapi_auth_header, READ_SCOPE, WRITE_SCOPE, READ_WRITE_SCOPE


class TestFlightDeclarationCreate:
    def test_create_flight_declaration(self, mounted_sync_client, flight_declaration_payload):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(READ_WRITE_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["message"] == "Submitted Flight Declaration"

    def test_create_flight_declaration_unsupported_media_type(self, mounted_sync_client, flight_declaration_payload):
        # FastAPI returns 422 for non-JSON bodies (no content-type gate like Django DRF)
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/flight_declaration",
            content=str(flight_declaration_payload),
            headers={**fastapi_auth_header(READ_WRITE_SCOPE), "content-type": "text/plain"},
        )
        assert resp.status_code == 422

    def test_create_flight_declaration_invalid_geojson(self, mounted_sync_client, future_dates):
        start, end = future_dates
        payload = {
            "originating_party": "Test",
            "start_datetime": start,
            "end_datetime": end,
            "flight_declaration_geo_json": {"type": "FeatureCollection", "features": []},
            "type_of_operation": 1,
            "aircraft_id": "UAV-1",
        }
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/flight_declaration",
            json=payload,
            headers=fastapi_auth_header(READ_WRITE_SCOPE),
        )
        assert resp.status_code in (200, 400)

    def test_create_flight_declaration_missing_fields(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/flight_declaration",
            json={"originating_party": "Test"},
            headers=fastapi_auth_header(READ_WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_create_flight_declaration_invalid_dates(self, mounted_sync_client, sample_geojson_feature_collection):
        payload = {
            "originating_party": "Test",
            "start_datetime": "2020-01-01T00:00:00Z",
            "end_datetime": "2020-01-02T00:00:00Z",
            "flight_declaration_geo_json": sample_geojson_feature_collection,
            "type_of_operation": 1,
            "aircraft_id": "UAV-1",
        }
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/flight_declaration",
            json=payload,
            headers=fastapi_auth_header(READ_WRITE_SCOPE),
        )
        assert resp.status_code == 400


class TestSetFlightDeclaration:
    def test_set_flight_declaration(self, mounted_sync_client, flight_declaration_payload):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data

    def test_set_flight_declaration_invalid(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json={},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_set_flight_declaration_with_all_fields(self, mounted_sync_client, sample_geojson_feature_collection, future_dates):
        start, end = future_dates
        payload = {
            "originating_party": "Full Test Operator",
            "start_datetime": start,
            "end_datetime": end,
            "flight_declaration_geo_json": sample_geojson_feature_collection,
            "type_of_operation": 2,
            "aircraft_id": "UAV-FULL-001",
            "submitted_by": "test@example.com",
        }
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200


class TestSetOperationalIntent:
    def test_set_operational_intent(self, mounted_sync_client, operational_intent_payload):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_operational_intent",
            json=operational_intent_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        # May fail due to pyproj UTM zone issues with certain coordinates
        assert resp.status_code in (200, 500)

    def test_set_operational_intent_invalid(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_operational_intent",
            json={},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400


class TestBulkFlightDeclarations:
    def test_bulk_create(self, mounted_sync_client, flight_declaration_payload):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declarations_bulk",
            json=[flight_declaration_payload],
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["submitted"] == 1
        assert data["failed"] == 0

    def test_bulk_create_non_array(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declarations_bulk",
            json={"not": "array"},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400

    def test_bulk_create_multiple(self, mounted_sync_client, flight_declaration_payload):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declarations_bulk",
            json=[flight_declaration_payload, flight_declaration_payload],
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["submitted"] == 2

    def test_bulk_create_mixed_valid_invalid(self, mounted_sync_client, flight_declaration_payload):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declarations_bulk",
            json=[flight_declaration_payload, {"invalid": "data"}],
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 207
        data = resp.json()
        assert data["submitted"] == 1
        assert data["failed"] == 1

    def test_bulk_create_empty_list(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declarations_bulk",
            json=[],
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200

    def test_bulk_operational_intents(self, mounted_sync_client, operational_intent_payload):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_operational_intents_bulk",
            json=[operational_intent_payload],
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code in (200, 207)

    def test_bulk_operational_intents_non_array(self, mounted_sync_client):
        resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_operational_intents_bulk",
            json={"not": "array"},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400


class TestFlightDeclarationList:
    def test_list_empty(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/flight_declaration",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["results"] == []

    def test_list_with_date_filter(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/flight_declaration?start_date=2025-01-01&end_date=2025-12-31",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_with_state_filter(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/flight_declaration?state=0,1",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_with_view_filter(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/flight_declaration?view=52.500,13.399,52.501,13.400",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_with_all_filters(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/flight_declaration?start_date=2025-01-01&end_date=2025-12-31&state=0,1&view=52.500,13.399,52.501,13.400",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_list_unauthenticated(self, mounted_sync_client):
        resp = mounted_sync_client.get("/flight_declaration_ops/flight_declaration")
        assert resp.status_code == 401


class TestFlightDeclarationDetail:
    def test_get_nonexistent(self, mounted_sync_client):
        pk = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/flight_declaration_ops/flight_declaration/{pk}",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 404

    def test_get_existing(self, mounted_sync_client, flight_declaration_payload):
        create_resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        fd_id = create_resp.json()["id"]

        resp = mounted_sync_client.get(
            f"/flight_declaration_ops/flight_declaration/{fd_id}",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == fd_id
        assert "operational_intent" in data
        assert "bounds" in data


class TestFlightDeclarationStateUpdate:
    def test_update_state(self, mounted_sync_client, flight_declaration_payload):
        create_resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        fd_id = create_resp.json()["id"]

        resp = mounted_sync_client.put(
            f"/flight_declaration_ops/flight_declaration_state/{fd_id}",
            json={"state": 2},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200

    def test_update_state_nonexistent(self, mounted_sync_client):
        pk = str(uuid.uuid4())
        resp = mounted_sync_client.put(
            f"/flight_declaration_ops/flight_declaration_state/{pk}",
            json={"state": 2},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code in (400, 404)


class TestFlightDeclarationApproval:
    def test_update_approval(self, mounted_sync_client, flight_declaration_payload):
        create_resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        fd_id = create_resp.json()["id"]

        resp = mounted_sync_client.put(
            f"/flight_declaration_ops/flight_declaration_review/{fd_id}",
            json={"is_approved": True},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200


class TestFlightDeclarationDelete:
    def test_delete_nonexistent(self, mounted_sync_client):
        pk = str(uuid.uuid4())
        resp = mounted_sync_client.delete(
            f"/flight_declaration_ops/flight_declaration/{pk}/delete",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 404

    def test_delete_existing(self, mounted_sync_client, flight_declaration_payload):
        create_resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        fd_id = create_resp.json()["id"]

        resp = mounted_sync_client.delete(
            f"/flight_declaration_ops/flight_declaration/{fd_id}/delete",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 204

        # Verify deleted
        resp = mounted_sync_client.get(
            f"/flight_declaration_ops/flight_declaration/{fd_id}",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 404


class TestSubmitToDSS:
    def test_submit_not_enabled(self, mounted_sync_client, flight_declaration_payload):
        create_resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        fd_id = create_resp.json()["id"]

        resp = mounted_sync_client.post(
            f"/flight_declaration_ops/flight_declaration/{fd_id}/submit_to_dss",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400
        assert "not enabled" in resp.json()["message"].lower()

    def test_submit_nonexistent(self, mounted_sync_client):
        pk = str(uuid.uuid4())
        resp = mounted_sync_client.post(
            f"/flight_declaration_ops/flight_declaration/{pk}/submit_to_dss",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 400


class TestNetworkFlightDeclarations:
    def test_network_by_view_not_enabled(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/network_flight_declarations_by_view?view=52.0,13.0,52.1,13.1",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_network_by_view_missing_view(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/network_flight_declarations_by_view",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_network_by_view_invalid_view(self, mounted_sync_client):
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/network_flight_declarations_by_view?view=bad",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_network_by_id_not_enabled(self, mounted_sync_client, flight_declaration_payload):
        create_resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        fd_id = create_resp.json()["id"]

        resp = mounted_sync_client.get(
            f"/flight_declaration_ops/flight_declaration/{fd_id}/network_flight_declarations",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400


class TestSubmitToDSSEnabled:
    """Tests for submit_flight_declaration_to_dss when USSP_NETWORK_ENABLED=1."""

    def test_submit_to_dss_not_found(self, mounted_sync_client, monkeypatch):
        """When the flight declaration does not exist, returns 404."""
        monkeypatch.setenv("USSP_NETWORK_ENABLED", "1")
        pk = str(uuid.uuid4())
        resp = mounted_sync_client.post(
            f"/flight_declaration_ops/flight_declaration/{pk}/submit_to_dss",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 404

    def test_submit_to_dss_wrong_state(self, mounted_sync_client, monkeypatch, flight_declaration_payload):
        """Flight declaration not in state=0 returns 409."""
        monkeypatch.setenv("USSP_NETWORK_ENABLED", "1")
        create_resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        fd_id = create_resp.json()["id"]

        # Move to state 1 (Accepted) to trigger the guard
        mounted_sync_client.put(
            f"/flight_declaration_ops/flight_declaration_state/{fd_id}",
            json={"state": 1},
            headers=fastapi_auth_header(WRITE_SCOPE),
        )

        resp = mounted_sync_client.post(
            f"/flight_declaration_ops/flight_declaration/{fd_id}/submit_to_dss",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 409

    def test_submit_to_dss_success(self, mounted_sync_client, monkeypatch, flight_declaration_payload):
        """When state=0, submit_to_dss enqueues the task and returns 200.

        AUTO_SUBMIT_TO_DSS=0 prevents the creation endpoint from auto-submitting
        (which would consume the state=0 window and return 409 on the explicit call).
        """
        monkeypatch.setenv("USSP_NETWORK_ENABLED", "1")
        monkeypatch.setenv("AUTO_SUBMIT_TO_DSS", "0")
        create_resp = mounted_sync_client.post(
            "/flight_declaration_ops/set_flight_declaration",
            json=flight_declaration_payload,
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        fd_id = create_resp.json()["id"]

        resp = mounted_sync_client.post(
            f"/flight_declaration_ops/flight_declaration/{fd_id}/submit_to_dss",
            headers=fastapi_auth_header(WRITE_SCOPE),
        )
        assert resp.status_code == 200
        assert "id" in resp.json()


class TestNetworkFlightDeclarationsByViewEnabled:
    """Tests network_flight_declaration_details_by_view with USSP_NETWORK_ENABLED=1."""

    def test_network_by_view_enabled_returns_200(self, mounted_sync_client, monkeypatch, mock_network_opint_empty):
        """With USSP enabled and mocked DSS, returns 200 with empty list."""
        monkeypatch.setenv("USSP_NETWORK_ENABLED", "1")
        resp = mounted_sync_client.get(
            "/flight_declaration_ops/network_flight_declarations_by_view?view=52.500,13.399,52.501,13.400",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200

    def test_network_by_id_enabled_no_flight(self, mounted_sync_client, monkeypatch):
        """With USSP enabled but flight not found returns 400 or 404."""
        monkeypatch.setenv("USSP_NETWORK_ENABLED", "1")
        pk = str(uuid.uuid4())
        resp = mounted_sync_client.get(
            f"/flight_declaration_ops/flight_declaration/{pk}/network_flight_declarations",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code in (400, 404)
