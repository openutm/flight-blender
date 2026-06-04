import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from flight_blender.config import settings

router = APIRouter()


async def _redis_pubsub_ws(websocket: WebSocket, channel_name: str) -> None:
    await websocket.accept()
    redis_client = await aioredis.from_url(settings.REDIS_BROKER_URL, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_name)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()
        await redis_client.aclose()


@router.websocket("/realtime/heartbeat/{session_id}")
async def heartbeat_ws(websocket: WebSocket, session_id: str) -> None:
    await _redis_pubsub_ws(websocket, f"heartbeat_{session_id}")


@router.websocket("/realtime/track/{session_id}")
async def track_ws(websocket: WebSocket, session_id: str) -> None:
    await _redis_pubsub_ws(websocket, f"track_{session_id}")
