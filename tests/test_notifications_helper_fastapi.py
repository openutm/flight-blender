"""FastAPI tests for notifications_ops endpoints."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


def _auth(scopes: list[str]) -> dict[str, str]:
    payload = {
        "sub": "test-user",
        "iss": "dummy",
        "aud": "testflight.flightblender.com",
        "scope": " ".join(scopes),
    }
    token = jwt.encode(payload, "secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _rsa_auth(scopes: list[str], private_key) -> dict[str, str]:
    payload = {
        "sub": "test-user",
        "iss": "https://passport.example.test",
        "aud": "testflight.flightblender.com",
        "scope": " ".join(scopes),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-key"})
    return {"Authorization": f"Bearer {token}"}


READ_SCOPE = ["flightblender.read"]
WRITE_SCOPE = ["flightblender.write"]


class TestListNotificationsFastAPI:
    def test_list_unauthenticated(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get("/notifications_ops/notifications")
        assert resp.status_code == 401

    def test_list_empty(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get("/notifications_ops/notifications", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "notifications" in data
        assert data["notifications"] == []

    def test_list_invalid_date(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/notifications_ops/notifications?start_date=not-a-date", headers=_auth(READ_SCOPE)
        )
        assert resp.status_code == 400
        assert "error" in resp.json()


class TestCreateNotificationFastAPI:
    def test_create_unauthenticated(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.post("/notifications_ops/notifications", json={"message": "test"})
        assert resp.status_code == 401

    def test_create_wrong_scope(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.post(
            "/notifications_ops/notifications", json={"message": "test"}, headers=_auth(READ_SCOPE)
        )
        assert resp.status_code == 403

    def test_create_success(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.post(
            "/notifications_ops/notifications",
            json={"message": "Flight state changed"},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["message"] == "Flight state changed"
        assert data["is_active"] is True
        assert "id" in data

    def test_create_with_session_id(self, mounted_fastapi_client):
        session_id = str(uuid.uuid4())
        resp = mounted_fastapi_client.post(
            "/notifications_ops/notifications",
            json={"message": "test notification", "session_id": session_id},
            headers=_auth(WRITE_SCOPE),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["session_id"] == session_id

    def test_create_then_list(self, mounted_fastapi_client):
        mounted_fastapi_client.post(
            "/notifications_ops/notifications",
            json={"message": "visible notification"},
            headers=_auth(WRITE_SCOPE),
        )
        resp = mounted_fastapi_client.get("/notifications_ops/notifications", headers=_auth(READ_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["notifications"]) >= 1
        messages = [n["message"] for n in data["notifications"]]
        assert "visible notification" in messages


class TestNotificationsAuthEnforcement:
    """Auth boundary checks with real JWT validation enabled."""

    @pytest.fixture
    def rsa_private_key(self, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.BYPASS_AUTH_TOKEN_VERIFICATION", False)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        jwk["kid"] = "test-key"

        async def fake_fetch_jwks(url: str):
            return {"keys": [jwk]}

        monkeypatch.setattr("flight_blender.infrastructure.auth.jwt_validator._fetch_jwks", fake_fetch_jwks)
        return private_key

    def test_missing_token_returns_401(self, mounted_fastapi_client, rsa_private_key):
        resp = mounted_fastapi_client.get("/notifications_ops/notifications")
        assert resp.status_code == 401

    def test_wrong_scope_returns_403(self, mounted_fastapi_client, rsa_private_key):
        resp = mounted_fastapi_client.get(
            "/notifications_ops/notifications", headers=_rsa_auth(["wrong.scope"], rsa_private_key)
        )
        assert resp.status_code == 403
