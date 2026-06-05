from fastapi import APIRouter, WebSocket

from flight_blender.services import realtime_svc

router = APIRouter()


@router.websocket("/ws/surveillance/heartbeat/{session_id}")
async def heartbeat_ws(websocket: WebSocket, session_id: str) -> None:
    await realtime_svc.redis_pubsub_websocket(websocket, f"heartbeat_{session_id}")


@router.websocket("/ws/surveillance/track/{session_id}")
async def track_ws(websocket: WebSocket, session_id: str) -> None:
    await realtime_svc.redis_pubsub_websocket(websocket, f"track_{session_id}")
