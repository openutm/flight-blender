web: uvicorn flight_blender.main:app --host 0.0.0.0 --port 8000
worker: celery -A flight_blender.tasks.celery_app worker --loglevel=info
