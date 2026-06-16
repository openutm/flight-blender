web: uvicorn flight_blender.asgi:application --host 0.0.0.0 --port ${PORT:-8000}
worker: celery worker --app=flight_blender
