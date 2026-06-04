import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flight_blender.settings")
django.setup()

from flight_blender.api.main import create_fastapi_app  # noqa: E402

application = create_fastapi_app()
