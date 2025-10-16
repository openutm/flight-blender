import json
from channels.generic.websocket import WebsocketConsumer
import logging

logger = logging.getLogger("django")


class HomeConsumer(WebsocketConsumer):
    def connect(self):
        self.accept()
        self.send(
            text_data=json.dumps(
                {"message": "Flight Blender WebSocket connection established."}
            )
        )

    def disconnect(self, close_code):
        logger.info(f"WebSocket disconnected with code: {close_code}")

    def receive(self, text_data):
        pass


class TrackConsumer(WebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.room_group_name = f"track_{self.session_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        self.accept()
        self.send(
            text_data=json.dumps(
                {"message": "TrackConsumer WebSocket connection established."}
            )
        )

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        logger.info(f"TrackConsumer WebSocket disconnected with code: {close_code}")

    async def receive(self, text_data):
        # Publish the received data to the group
        await self.channel_layer.group_send(
            self.room_group_name, {"type": "publish_data", "data": text_data}
        )

    def publish_data(self, event):
        # Send the published data to the WebSocket
        self.send(text_data=json.dumps({"published_data": event["data"]}))


class HeartBeatConsumer(WebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.room_group_name = f"heartbeat_{self.session_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        self.accept()
        self.send(
            text_data=json.dumps(
                {"message": "HeartBeatConsumer WebSocket connection established."}
            )
        )

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        logger.info(f"HeartBeatConsumer WebSocket disconnected with code: {close_code}")

    async def receive(self, text_data):
        # Publish the received data to the group
        await self.channel_layer.group_send(
            self.room_group_name, {"type": "publish_data", "data": text_data}
        )

    def publish_data(self, event):
        # Send the published data to the WebSocket
        self.send(text_data=json.dumps({"published_data": event["data"]}))
