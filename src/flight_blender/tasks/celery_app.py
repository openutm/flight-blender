"""
Celery application factory (framework-agnostic).
"""

from celery import Celery

from flight_blender.config import get_settings

settings = get_settings()

celery_app = Celery(
    "flight_blender",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "flight_blender.tasks.flight_feed",
        "flight_blender.tasks.flight_declaration",
        "flight_blender.tasks.rid",
        "flight_blender.tasks.conformance",
        "flight_blender.tasks.surveillance",
        "flight_blender.tasks.geo_fence",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "cleanup-old-heartbeat-events": {
            "task": "cleanup_old_heartbeat_events",
            "schedule": 3600.0,  # Every hour
        },
    },
)
