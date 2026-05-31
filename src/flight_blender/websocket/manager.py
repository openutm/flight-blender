"""
WebSocket connection manager for broadcasting surveillance / RID events.
"""

from __future__ import annotations

from typing import Dict, Set

from fastapi import WebSocket
from loguru import logger


class ConnectionManager:
    """
    Maintains per-channel sets of active WebSocket connections and provides
    broadcast helpers used by Celery tasks via a shared in-process event loop.
    """

    def __init__(self) -> None:
        self._channels: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        await websocket.accept()
        self._channels.setdefault(channel, set()).add(websocket)
        logger.info("WebSocket connected to channel '%s' (%d total)", channel, len(self._channels[channel]))

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        channel_set = self._channels.get(channel, set())
        channel_set.discard(websocket)
        logger.info("WebSocket disconnected from channel '%s' (%d remaining)", channel, len(channel_set))

    async def broadcast_json(self, channel: str, data: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._channels.get(channel, [])):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, channel)

    async def broadcast_text(self, channel: str, text: str) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._channels.get(channel, [])):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, channel)

    def channel_size(self, channel: str) -> int:
        return len(self._channels.get(channel, []))


# Module-level singleton used by routers and tasks.
manager = ConnectionManager()
