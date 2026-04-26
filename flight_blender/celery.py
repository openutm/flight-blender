import os

from celery import Celery
from celery.signals import task_postrun

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flight_blender.settings")
app = Celery(
    "flight_blender",
    include=["conformance_monitoring_operations.tasks", "surveillance_monitoring_operations.tasks"],
    broker_connection_retry_on_startup=True,
)

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks(related_name="tasks")
app.autodiscover_tasks(related_name="custom_tasks")


@task_postrun.connect
def close_db_connections_after_task(**kwargs):
    """Close stale DB connections after each Celery task.

    Under ASGI + prefork, Django's built-in fixup does not always release
    connections promptly, leading to PostgreSQL connection exhaustion.
    """
    from django.db import close_old_connections

    close_old_connections()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
