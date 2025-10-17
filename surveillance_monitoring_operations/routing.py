from django.urls import path

from .consumers import HeartBeatConsumer, HomeConsumer, TrackConsumer

websocket_urlpatterns = [
    path("ws/surveillance/", HomeConsumer.as_asgi()),
    path("ws/surveillance/track/<uuid:session_id>", TrackConsumer.as_asgi()),
    path("ws/surveillance/heartbeat/<uuid:session_id>", HeartBeatConsumer.as_asgi()),
]
