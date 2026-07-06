from unittest.mock import patch

from flight_blender.services.misc_svc import get_signing_public_keys


class TestHome:
    def test_home_renders_template(self, fastapi_client):
        resp = fastapi_client.get("/")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "<title>Flight Blender</title>" in resp.text
        assert "Your instance of Flight Blender is working" in resp.text

    def test_home_automatically_connects_when_auth_bypass_is_enabled(self, fastapi_client):
        with patch("flight_blender.api.routers.misc_api.settings") as mock_settings:
            mock_settings.BYPASS_AUTH_TOKEN_VERIFICATION = True
            resp = fastapi_client.get("/")

        assert resp.status_code == 200
        assert "const authBypassEnabled = true;" in resp.text

    def test_home_does_not_automatically_connect_when_auth_bypass_is_disabled(self, fastapi_client):
        with patch("flight_blender.api.routers.misc_api.settings") as mock_settings:
            mock_settings.BYPASS_AUTH_TOKEN_VERIFICATION = False
            resp = fastapi_client.get("/")

        assert resp.status_code == 200
        assert "const authBypassEnabled = false;" in resp.text


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


# ---------------------------------------------------------------------------
# Misc service additional coverage
# ---------------------------------------------------------------------------


class TestMiscServiceCoverage:
    """Additional tests for misc_svc."""

    def test_get_signing_public_keys_with_secret_key(self):
        """Test get_signing_public_keys with secret key."""
        with patch("flight_blender.services.misc_svc.settings") as mock_settings:
            mock_settings.SECRET_KEY = "test-secret-key"

            result = get_signing_public_keys()

            assert isinstance(result, list)

    def test_get_signing_public_keys_without_secret_key(self):
        """Test get_signing_public_keys without secret key."""
        with patch("flight_blender.services.misc_svc.settings") as mock_settings:
            mock_settings.SECRET_KEY = ""

            result = get_signing_public_keys()

            assert result == []

    def test_get_signing_public_keys_with_invalid_key(self):
        """Test get_signing_public_keys with invalid key."""
        with patch("flight_blender.services.misc_svc.settings") as mock_settings:
            mock_settings.SECRET_KEY = "invalid-key"

            result = get_signing_public_keys()

            assert result == []
