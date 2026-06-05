from celery import Celery

from flight_blender.config import settings

app = Celery(
    "flight_blender",
    broker=settings.REDIS_BROKER_URL,
    broker_connection_retry_on_startup=True,
    include=[
        "flight_blender.tasks.geo_fence_task",
        "flight_blender.tasks.surveillance_task",
        "flight_blender.tasks.flight_feed_task",
        "flight_blender.tasks.flight_declarations_task",
        "flight_blender.tasks.conformance_task",
        "flight_blender.tasks.rid_task",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
