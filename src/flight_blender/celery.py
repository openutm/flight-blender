from celery import Celery
from celery.signals import task_postrun

from flight_blender.config import settings

app = Celery(
    "flight_blender",
    broker=settings.REDIS_BROKER_URL,
    broker_connection_retry_on_startup=True,
    include=[
        "flight_blender.infrastructure.celery.tasks.geo_fence",
        "flight_blender.infrastructure.celery.tasks.surveillance",
        "flight_blender.infrastructure.celery.tasks.flight_feed",
        "flight_blender.infrastructure.celery.tasks.flight_declarations",
        "flight_blender.infrastructure.celery.tasks.conformance",
        "flight_blender.infrastructure.celery.tasks.rid",
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
