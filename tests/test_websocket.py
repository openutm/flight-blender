"""
Tests for WebSocket connection manager and endpoints.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# ConnectionManager unit tests
# ---------------------------------------------------------------------------
class TestConnectionManager:
    def _make_ws(self) -> MagicMock:
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.mark.anyio
    async def test_connect_accepts_and_tracks(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        ws = self._make_ws()
        await mgr.connect(ws, "test_channel")

        ws.accept.assert_called_once()
        assert mgr.channel_size("test_channel") == 1

    @pytest.mark.anyio
    async def test_connect_multiple_websockets_same_channel(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        ws1 = self._make_ws()
        ws2 = self._make_ws()
        await mgr.connect(ws1, "chan")
        await mgr.connect(ws2, "chan")

        assert mgr.channel_size("chan") == 2

    def test_disconnect_removes_websocket(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        ws = self._make_ws()
        # Manually insert into channels dict to simulate a connected WS
        mgr._channels["chan"] = {ws}
        mgr.disconnect(ws, "chan")

        assert mgr.channel_size("chan") == 0

    def test_disconnect_nonexistent_channel_no_error(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        ws = self._make_ws()
        # Should not raise even if channel doesn't exist
        mgr.disconnect(ws, "no_such_channel")

    def test_channel_size_empty_channel(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        assert mgr.channel_size("not_registered") == 0

    @pytest.mark.anyio
    async def test_broadcast_json_sends_to_all(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        ws1 = self._make_ws()
        ws2 = self._make_ws()
        mgr._channels["chan"] = {ws1, ws2}

        await mgr.broadcast_json("chan", {"key": "value"})

        ws1.send_json.assert_called_once_with({"key": "value"})
        ws2.send_json.assert_called_once_with({"key": "value"})

    @pytest.mark.anyio
    async def test_broadcast_json_removes_dead_connections(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        dead_ws = self._make_ws()
        dead_ws.send_json.side_effect = Exception("connection closed")
        mgr._channels["chan"] = {dead_ws}

        await mgr.broadcast_json("chan", {"key": "value"})

        # Dead connection should be removed
        assert mgr.channel_size("chan") == 0

    @pytest.mark.anyio
    async def test_broadcast_text_sends_to_all(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        ws = self._make_ws()
        mgr._channels["chan"] = {ws}

        await mgr.broadcast_text("chan", "hello world")

        ws.send_text.assert_called_once_with("hello world")

    @pytest.mark.anyio
    async def test_broadcast_text_removes_dead_connections(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        dead_ws = self._make_ws()
        dead_ws.send_text.side_effect = Exception("connection closed")
        mgr._channels["chan"] = {dead_ws}

        await mgr.broadcast_text("chan", "hello")

        assert mgr.channel_size("chan") == 0

    @pytest.mark.anyio
    async def test_broadcast_to_empty_channel_no_error(self):
        from flight_blender.websocket.manager import ConnectionManager

        mgr = ConnectionManager()
        # Should not raise
        await mgr.broadcast_json("nonexistent", {"data": 1})
        await mgr.broadcast_text("nonexistent", "text")


# ---------------------------------------------------------------------------
# WebSocket helper function tests
# ---------------------------------------------------------------------------
class TestHandleWsAuth:
    @pytest.mark.anyio
    async def test_handle_ws_auth_consumes_message(self):
        from flight_blender.websocket import _handle_ws_auth

        ws = MagicMock()
        ws.receive_text = AsyncMock(return_value="Bearer token123")

        # Should complete without error
        await _handle_ws_auth(ws)
        ws.receive_text.assert_called_once()

    @pytest.mark.anyio
    async def test_handle_ws_auth_timeout_is_ignored(self):
        import asyncio

        from flight_blender.websocket import _handle_ws_auth

        ws = MagicMock()

        async def slow_receive():
            await asyncio.sleep(10)
            return "msg"

        ws.receive_text = slow_receive

        # Should complete even when timeout fires
        await _handle_ws_auth(ws)

    @pytest.mark.anyio
    async def test_handle_ws_auth_exception_is_ignored(self):
        from flight_blender.websocket import _handle_ws_auth

        ws = MagicMock()
        ws.receive_text = AsyncMock(side_effect=RuntimeError("connection error"))

        # Exception should be swallowed
        await _handle_ws_auth(ws)


# ---------------------------------------------------------------------------
# JWT Bearer async tests
# ---------------------------------------------------------------------------
anyio_backend = "asyncio"


class TestJwtBearerAsync:
    @pytest.mark.anyio
    async def test_get_token_payload_bypass_mode(self):
        """In bypass mode, payload returns all scopes without checking token."""
        from flight_blender.auth.jwt_bearer import _get_token_payload

        payload = await _get_token_payload(None)
        assert "blender.read" in payload.get("scope", "")
        assert "blender.write" in payload.get("scope", "")

    @pytest.mark.anyio
    async def test_get_token_payload_missing_credentials_raises_401(self):
        """When not in bypass mode and no credentials, should raise HTTP 401."""
        from fastapi import HTTPException
        from flight_blender.auth.jwt_bearer import _get_token_payload
        from flight_blender.config import get_settings

        settings = get_settings()
        original = settings.bypass_auth_token_verification
        settings.bypass_auth_token_verification = False
        try:
            with pytest.raises(HTTPException) as exc_info:
                await _get_token_payload(None)
            assert exc_info.value.status_code == 401
        finally:
            settings.bypass_auth_token_verification = original

    @pytest.mark.anyio
    async def test_get_token_payload_invalid_token_raises_401(self):
        """Invalid JWT token should raise HTTP 401."""
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        from flight_blender.auth.jwt_bearer import _get_token_payload
        from flight_blender.config import get_settings

        settings = get_settings()
        original = settings.bypass_auth_token_verification
        settings.bypass_auth_token_verification = False
        settings.auth_server_jwks_uri = ""  # no jwks uri → decode without verify
        try:
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.valid.token")
            with pytest.raises(HTTPException) as exc_info:
                await _get_token_payload(creds)
            assert exc_info.value.status_code == 401
        finally:
            settings.bypass_auth_token_verification = original

    @pytest.mark.anyio
    async def test_require_scope_passes_when_scope_present(self):
        """Dependency should pass through payload when required scope is in token."""
        from flight_blender.auth.jwt_bearer import require_scope

        dep = require_scope("blender.read")
        payload = {"scope": "blender.read blender.write"}
        result = await dep(payload)
        assert result == payload

    @pytest.mark.anyio
    async def test_require_scope_raises_403_when_scope_missing(self):
        """Dependency should raise 403 when required scope is missing from token."""
        from fastapi import HTTPException
        from flight_blender.auth.jwt_bearer import require_scope

        dep = require_scope("blender.admin")
        payload = {"scope": "blender.read"}
        with pytest.raises(HTTPException) as exc_info:
            await dep(payload)
        assert exc_info.value.status_code == 403
        assert "blender.admin" in exc_info.value.detail
