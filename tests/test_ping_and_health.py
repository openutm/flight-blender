import pytest


class TestPing:
    def test_ping_returns_pong(self, fastapi_client):
        resp = fastapi_client.get("/ping")
        assert resp.status_code == 200
        assert resp.json() == {"message": "pong"}


class TestSigningPublicKey:
    def test_returns_keys_or_empty(self, fastapi_client):
        resp = fastapi_client.get("/signing_public_key")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "keys" in data
