"""
WebSocket authentication tests.

The surveillance WebSocket endpoints were unauthenticated: anyone able to reach
the server could subscribe to any session's heartbeat/track channel. These tests
pin that, with the auth bypass switched OFF, the handshake is rejected (closed
before accept) unless a valid bearer token carrying the read scope is supplied
via the ``token`` query parameter. With the bypass ON (the suite default) the
connection works without a token, preserving the existing dev/test behaviour.
"""

import time
import types
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import flight_blender.auth.jwt_bearer as jb
from flight_blender.main import create_app

HEARTBEAT = "/ws/surveillance/heartbeat/test-session"
TRACK = "/ws/surveillance/track/test-session"


@pytest.fixture
def ws_client():
    with patch(
        "flight_blender.websocket.async_read_all_observations",
        new_callable=AsyncMock,
        return_value=[],
    ):
        yield TestClient(create_app())


def _keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _settings(**overrides):
    base = dict(
        bypass_auth_token_verification=False,
        auth_server_jwks_uri="",
        auth_audience="",
        flightblender_read_scope="blender.read",
        flightblender_write_scope="blender.write",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture
def auth_enabled(monkeypatch):
    """Bypass OFF + JWKS configured with a fake signing key. Returns a token factory."""
    private_key, public_key = _keypair()

    class _FakeJWKClient:
        def __init__(self, *_a, **_kw):
            pass

        def get_signing_key_from_jwt(self, _token):
            return types.SimpleNamespace(key=public_key)

    monkeypatch.setattr(jb.jwt, "PyJWKClient", _FakeJWKClient)
    monkeypatch.setattr(jb, "settings", _settings(auth_server_jwks_uri="https://jwks.test"))
    jb._get_jwks_client.cache_clear()

    def _token(scope: str = "blender.read blender.write") -> str:
        return jwt.encode(
            {"exp": int(time.time()) + 3600, "iss": "https://issuer.test", "scope": scope},
            private_key,
            algorithm="RS256",
        )

    return _token


def test_heartbeat_rejected_without_token_when_auth_enabled(ws_client, monkeypatch):
    monkeypatch.setattr(jb, "settings", _settings())
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(HEARTBEAT) as ws:
            ws.receive_json()


def test_track_rejected_without_token_when_auth_enabled(ws_client, monkeypatch):
    monkeypatch.setattr(jb, "settings", _settings())
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(TRACK) as ws:
            ws.receive_json()


def test_heartbeat_rejected_with_insufficient_scope(ws_client, auth_enabled):
    token = auth_enabled(scope="some.other.scope")
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(f"{HEARTBEAT}?token={token}") as ws:
            ws.receive_json()


def test_heartbeat_accepted_with_valid_token(ws_client, auth_enabled):
    token = auth_enabled()
    with ws_client.websocket_connect(f"{HEARTBEAT}?token={token}") as ws:
        ws.send_text("auth")  # consumed by _handle_ws_auth so the loop starts promptly
        data = ws.receive_json()
        assert "heartbeat_data" in data


def test_heartbeat_allowed_without_token_when_bypass_enabled(ws_client):
    """Suite default: bypass on -> no token required (regression guard)."""
    with ws_client.websocket_connect(HEARTBEAT) as ws:
        ws.send_text("auth")
        data = ws.receive_json()
        assert "heartbeat_data" in data
