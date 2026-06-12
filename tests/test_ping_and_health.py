import pytest
from unittest.mock import patch


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
        from flight_blender.services.misc_svc import get_signing_public_keys

        with patch('flight_blender.services.misc_svc.settings') as mock_settings:
            mock_settings.SECRET_KEY = "test-secret-key"

            result = get_signing_public_keys()

            assert isinstance(result, list)

    def test_get_signing_public_keys_without_secret_key(self):
        """Test get_signing_public_keys without secret key."""
        from flight_blender.services.misc_svc import get_signing_public_keys

        with patch('flight_blender.services.misc_svc.settings') as mock_settings:
            mock_settings.SECRET_KEY = ""

            result = get_signing_public_keys()

            assert result == []

    def test_get_signing_public_keys_with_invalid_key(self):
        """Test get_signing_public_keys with invalid key."""
        from flight_blender.services.misc_svc import get_signing_public_keys

        with patch('flight_blender.services.misc_svc.settings') as mock_settings:
            mock_settings.SECRET_KEY = "invalid-key"

            result = get_signing_public_keys()

            assert result == []
