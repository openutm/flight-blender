"""WebSocket package."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from loguru import logger

from flight_blender.auth.jwt_bearer import verify_bearer_token
from flight_blender.config import get_settings
from flight_blender.websocket.manager import ConnectionManager, manager

settings = get_settings()

ws_router = APIRouter()

__all__ = ["manager", "ConnectionManager", "ws_router"]


async def _authorize_ws(websocket: WebSocket, required_scope: str) -> bool:
    """Authorise a WebSocket handshake before accepting it.

    The bearer token is read from the ``token`` query parameter (browsers cannot
    set Authorization headers on WebSocket connections). Honors the auth bypass
    via ``verify_bearer_token``. On failure the handshake is closed with a
    policy-violation code and ``False`` is returned so the caller can stop.
    """
    token = websocket.query_params.get("token")
    try:
        payload = verify_bearer_token(token)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    if required_scope not in set((payload.get("scope") or "").split()):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    return True


async def _handle_ws_auth(websocket: WebSocket) -> None:
    """Receive the optional auth message sent by the verification toolkit client.

    The base client sends the Authorization header as the first text message
    after connecting. We simply consume it so it doesn't interfere with data
    messages. In production this is where token validation would happen.
    """
    try:
        # The client sends auth as the first message; consume it with a short timeout
        await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
        logger.debug("WebSocket auth message received (consumed)")
    except asyncio.TimeoutError:
        # No auth message within timeout — that's fine for other clients
        pass
    except Exception:  # nosec B110
        pass


@ws_router.websocket("/ws/surveillance/heartbeat/{session_id}")
async def websocket_heartbeat(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time heartbeat messages.

    The verification toolkit connects here and expects JSON messages of the form::

        {"heartbeat_data": {"timestamp": "...", ...}}
    """
    if not await _authorize_ws(websocket, settings.flightblender_read_scope):
        return
    channel = f"heartbeat:{session_id}"
    await manager.connect(websocket, channel)
    logger.info("Heartbeat WebSocket connected for session %s", session_id)

    # Consume the auth message the verification toolkit sends
    await _handle_ws_auth(websocket)

    try:
        while True:
            # Derive the heartbeat SLA metrics from the live observation stream
            # instead of emitting hard-coded "healthy" constants.
            from flight_blender.common.redis_stream_operations import read_all_observations
            from flight_blender.tasks.surveillance import compute_sdsp_heartbeat

            now = datetime.now(tz=timezone.utc)
            observations = read_all_observations(session_id=session_id, count=500)
            heartbeat_data = compute_sdsp_heartbeat(observations, now=now)
            await websocket.send_json({"heartbeat_data": heartbeat_data})
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        manager.disconnect(websocket, channel)
        logger.info("Heartbeat WebSocket disconnected for session %s", session_id)
    except Exception:
        manager.disconnect(websocket, channel)


@ws_router.websocket("/ws/surveillance/track/{session_id}")
async def websocket_track(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time track messages.

    The verification toolkit connects here and expects JSON messages of the form::

        {"track_data": [{...TrackMessage...}, ...]}
    """
    if not await _authorize_ws(websocket, settings.flightblender_read_scope):
        return
    channel = f"track:{session_id}"
    await manager.connect(websocket, channel)
    logger.info("Track WebSocket connected for session %s", session_id)

    # Consume the auth message the verification toolkit sends
    await _handle_ws_auth(websocket)

    try:
        while True:
            # Read latest observations from Redis stream
            from flight_blender.common.redis_stream_operations import read_all_observations
            from flight_blender.services.traffic_data_fuser import DefaultTrafficDataFuser

            observations = read_all_observations(session_id=session_id, count=500)

            if observations:
                fuser = DefaultTrafficDataFuser(session_id=session_id, raw_observations=observations)
                tracks = fuser.generate_track_messages()
                track_data = [
                    {
                        "sdsdp_identifier": t.sdsdp_identifier,
                        "unique_aircraft_identifier": t.unique_aircraft_identifier,
                        "state": t.state,
                        "timestamp": t.timestamp,
                        "source": t.source,
                        "track_state": t.track_state,
                    }
                    for t in tracks
                ]
                await websocket.send_json({"track_data": track_data})
            else:
                await websocket.send_json({"track_data": []})
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        manager.disconnect(websocket, channel)
        logger.info("Track WebSocket disconnected for session %s", session_id)
    except Exception:
        manager.disconnect(websocket, channel)
