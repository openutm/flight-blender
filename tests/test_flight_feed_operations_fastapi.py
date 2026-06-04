"""FastAPI tests for flight_stream endpoints."""
import json
import uuid
from unittest.mock import patch

import jwt
import pytest


def _auth(scopes: list[str]) -> dict[str, str]:
    payload = {
        "sub": "test-user",
        "iss": "dummy",
        "aud": "testflight.flightblender.com",
        "scope": " ".join(scopes),
    }
    token = jwt.encode(payload, "secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


READ_SCOPE = ["flightblender.read"]
WRITE_SCOPE = ["flightblender.write"]
GA_TEST_SCOPE = ["geo-awareness.test"]

OBSERVATION = {
    "lat_dd": 52.5,
    "lon_dd": 13.4,
    "altitude_mm": 50000,
    "traffic_source": 1,
    "source_type": 1,
    "icao_address": "ABC123",
    "timestamp": 1717243200,
}


class TestSetAirTrafficFastAPI:
    def test_unauthenticated(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(f"/flight_stream/set_air_traffic/{session_id}", json={"observations": [OBSERVATION]})
        assert resp.status_code == 401

    def test_unsupported_media_type(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            content=b"{}",
            headers={**_auth(WRITE_SCOPE), "content-type": "text/plain"},
        )
        assert resp.status_code == 422

    def test_missing_observations(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json={},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 422

    def test_invalid_observation(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json={"observations": [{"invalid": "data"}]},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 422

    def test_valid_observation(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json={"observations": [OBSERVATION]},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_valid_with_metadata(self, fastapi_client):
        session_id = str(uuid.uuid4())
        obs = {**OBSERVATION, "metadata": {"speed": 100}}
        resp = fastapi_client.post(
            f"/flight_stream/set_air_traffic/{session_id}",
            json={"observations": [obs]},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 201


class TestBulkSetAirTrafficFastAPI:
    def test_unauthenticated(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(f"/flight_stream/bulk_set_air_traffic/{session_id}", json={"observations": [OBSERVATION]})
        assert resp.status_code == 401

    def test_missing_observations(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            json={},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 422

    def test_valid(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            json={"observations": [OBSERVATION]},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 201

    def test_unsupported_media_type(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.post(
            f"/flight_stream/bulk_set_air_traffic/{session_id}",
            content=b"{}",
            headers={**_auth(WRITE_SCOPE), "content-type": "text/plain"},
        )
        assert resp.status_code == 422


class TestGetAirTrafficFastAPI:
    def test_unauthenticated(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.get(f"/flight_stream/get_air_traffic/{session_id}?view=52.500,13.399,52.501,13.400")
        assert resp.status_code == 401

    def test_missing_view(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.get(f"/flight_stream/get_air_traffic/{session_id}", headers=_auth(READ_SCOPE))
        assert resp.status_code == 400

    def test_invalid_view(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.get(f"/flight_stream/get_air_traffic/{session_id}?view=bad", headers=_auth(READ_SCOPE))
        assert resp.status_code == 400

    def test_valid_view_empty(self, fastapi_client):
        session_id = str(uuid.uuid4())
        resp = fastapi_client.get(
            f"/flight_stream/get_air_traffic/{session_id}?view=52.500,13.399,52.501,13.400",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 200
        assert "observations" in resp.json()


class TestStartOpenskyFeedFastAPI:
    @pytest.fixture(autouse=True)
    def _mock_opensky_task(self):
        with patch("flight_blender.flight_feed.tasks.start_opensky_network_stream.delay"):
            yield

    def test_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/flight_stream/start_opensky_feed?view=52.500,13.399,52.501,13.400")
        assert resp.status_code == 401

    def test_missing_view(self, fastapi_client):
        resp = fastapi_client.get("/flight_stream/start_opensky_feed", headers=_auth(READ_SCOPE))
        assert resp.status_code == 400

    def test_invalid_view(self, fastapi_client):
        resp = fastapi_client.get("/flight_stream/start_opensky_feed?view=bad", headers=_auth(READ_SCOPE))
        assert resp.status_code == 400

    def test_valid_view(self, fastapi_client):
        resp = fastapi_client.get(
            "/flight_stream/start_opensky_feed?view=52.500,13.399,52.501,13.400",
            headers=_auth(READ_SCOPE),
        )
        assert resp.status_code == 200
        assert "message" in resp.json()


class TestPublicKeysFastAPI:
    def test_list_unauthenticated(self, fastapi_client):
        resp = fastapi_client.get("/flight_stream/public_keys/")
        assert resp.status_code == 401

    def test_list_empty(self, fastapi_client):
        resp = fastapi_client.get("/flight_stream/public_keys/", headers=_auth(GA_TEST_SCOPE))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_retrieve(self, fastapi_client):
        resp = fastapi_client.post(
            "/flight_stream/public_keys/",
            json={"key_id": "test-key", "url": "https://example.com/key.json", "is_active": True},
            headers=_auth(GA_TEST_SCOPE),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["key_id"] == "test-key"
        pk = data["id"]

        resp2 = fastapi_client.get(f"/flight_stream/public_keys/{pk}/", headers=_auth(GA_TEST_SCOPE))
        assert resp2.status_code == 200
        assert resp2.json()["key_id"] == "test-key"

    def test_get_not_found(self, fastapi_client):
        pk = str(uuid.uuid4())
        resp = fastapi_client.get(f"/flight_stream/public_keys/{pk}/", headers=_auth(GA_TEST_SCOPE))
        assert resp.status_code == 404

    def test_delete_not_found(self, fastapi_client):
        pk = str(uuid.uuid4())
        resp = fastapi_client.delete(f"/flight_stream/public_keys/{pk}/", headers=_auth(GA_TEST_SCOPE))
        assert resp.status_code == 404

    def test_create_and_delete(self, fastapi_client):
        resp = fastapi_client.post(
            "/flight_stream/public_keys/",
            json={"key_id": "del-key", "url": "https://example.com/key.json"},
            headers=_auth(GA_TEST_SCOPE),
        )
        assert resp.status_code == 201
        pk = resp.json()["id"]

        del_resp = fastapi_client.delete(f"/flight_stream/public_keys/{pk}/", headers=_auth(GA_TEST_SCOPE))
        assert del_resp.status_code == 204
