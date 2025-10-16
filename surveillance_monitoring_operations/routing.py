from django.urls import re_path
from .consumers import HomeConsumer, TrackConsumer, HeartBeatConsumer

websocket_urlpatterns = [
    re_path(r'ws/surveillance/$', HomeConsumer.as_asgi()),
    re_path(r'ws/surveillance/track/(?P<session_id>[0-9a-f-]+)', TrackConsumer.as_asgi()),
    re_path(r'ws/surveillance/heartbeat/(?P<session_id>[0-9a-f-]+)', HeartBeatConsumer.as_asgi()),
]