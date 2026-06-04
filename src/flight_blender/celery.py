from celery import Celery
from celery.signals import task_postrun

from flight_blender.config import settings

app = Celery(
    "flight_blender",
    broker=settings.REDIS_BROKER_URL,
    broker_connection_retry_on_startup=True,
    include=[
        "flight_blender.geo_fence.tasks",
        "flight_blender.surveillance.tasks",
        "flight_blender.flight_feed.tasks",
        "flight_blender.flight_declarations.tasks",
        "flight_blender.conformance.tasks",
        "flight_blender.rid.tasks",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@task_postrun.connect
def close_db_connections_after_task(**kwargs):
    """Return connections to the pool after each Celery task."""
    from flight_blender.infrastructure.database.session import engine

    engine.dispose(close=False)


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
