"""FastAPI tests for constraint_ops endpoints."""
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


CONSTRAINT_SCOPE = ["utm.constraint_processing"]
READ_SCOPE = ["flightblender.read"]


class TestConstraintDetailsFastAPI:
    def test_list_constraint_details_unauthenticated(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get("/constraint_ops/constraint_details")
        assert resp.status_code == 401

    def test_list_constraint_details_wrong_scope(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get("/constraint_ops/constraint_details", headers=_auth(READ_SCOPE))
        assert resp.status_code == 403

    def test_list_constraint_details_empty(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get("/constraint_ops/constraint_details", headers=_auth(CONSTRAINT_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "constraint_details" in data
        assert data["constraint_details"] == []

    def test_get_constraint_detail_not_found(self, mounted_fastapi_client):
        nonexistent = str(uuid.uuid4())
        resp = mounted_fastapi_client.get(f"/constraint_ops/constraint_details/{nonexistent}", headers=_auth(CONSTRAINT_SCOPE))
        assert resp.status_code == 404
        assert resp.json() == {"message": "Not found"}


class TestConstraintReferencesFastAPI:
    def test_list_constraint_references_unauthenticated(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get("/constraint_ops/constraint_references")
        assert resp.status_code == 401

    def test_list_constraint_references_empty(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get("/constraint_ops/constraint_references", headers=_auth(CONSTRAINT_SCOPE))
        assert resp.status_code == 200
        data = resp.json()
        assert "constraint_references" in data
        assert data["constraint_references"] == []

    def test_get_constraint_reference_not_found(self, mounted_fastapi_client):
        nonexistent = str(uuid.uuid4())
        resp = mounted_fastapi_client.get(
            f"/constraint_ops/constraint_references/{nonexistent}", headers=_auth(CONSTRAINT_SCOPE)
        )
        assert resp.status_code == 404
        assert resp.json() == {"message": "Not found"}


class TestConstraintAuthEnforcement:
    """Auth boundary checks with real JWT validation enabled."""

    @pytest.fixture
    def rsa_private_key(self, monkeypatch):
        monkeypatch.setattr("flight_blender.config.settings.BYPASS_AUTH_TOKEN_VERIFICATION", False)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        jwk["kid"] = "test-key"

        async def fake_fetch_jwks(url: str):
            return {"keys": [jwk]}

        monkeypatch.setattr("flight_blender.auth.jwt_validator._fetch_jwks", fake_fetch_jwks)
        return private_key

    def test_missing_token_returns_401(self, mounted_fastapi_client, rsa_private_key):
        resp = mounted_fastapi_client.get("/constraint_ops/constraint_details")
        assert resp.status_code == 401

    def test_wrong_scope_returns_403(self, mounted_fastapi_client, rsa_private_key):
        resp = mounted_fastapi_client.get(
            "/constraint_ops/constraint_details", headers=_rsa_auth(["wrong.scope"], rsa_private_key)
        )
        assert resp.status_code == 403
