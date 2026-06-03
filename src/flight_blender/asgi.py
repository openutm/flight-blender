"""
ASGI config for flight_blender project.
"""

import os

import django
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from starlette.applications import Starlette
from starlette.routing import Mount

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flight_blender.settings")
django.setup()

from flight_blender.api.main import MIGRATED_PREFIXES, create_fastapi_app  # noqa: E402
from flight_blender.surveillance.routing import websocket_urlpatterns  # noqa: E402

django_asgi_app = get_asgi_application()
fastapi_app = create_fastapi_app()

_routes = [Mount(p, app=fastapi_app) for p in MIGRATED_PREFIXES]
_routes.append(Mount("/", app=django_asgi_app))

application = ProtocolTypeRouter(
    {
        "http": Starlette(routes=_routes),
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
