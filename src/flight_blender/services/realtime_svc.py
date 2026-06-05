import redis.asyncio as aioredis
from fastapi import WebSocket, WebSocketDisconnect

from flight_blender.config import settings


async def redis_pubsub_websocket(websocket: WebSocket, channel_name: str) -> None:
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
