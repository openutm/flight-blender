import pytest


@pytest.mark.django_db
class TestPing:
    def test_ping_returns_pong(self, client):
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.json() == {"message": "pong"}


@pytest.mark.django_db
class TestSigningPublicKey:
    def test_returns_keys_or_empty(self, client):
        resp = client.get("/signing_public_key")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


@pytest.mark.django_db
class TestRootEndpoints:
    def test_home_view(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_realtime_view(self, client):
        resp = client.get("/realtime")
        assert resp.status_code == 200
