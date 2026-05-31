"""Celery tasks package."""

from flight_blender.tasks import notification
from flight_blender.tasks.celery_app import celery_app

__all__ = ["celery_app", "notification"]
