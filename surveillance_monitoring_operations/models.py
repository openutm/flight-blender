from django.db import models
import uuid

from django.utils import timezone
from datetime import timedelta


def get_thirty_minutes_from_now():

    return timezone.now() + timedelta(minutes=30)


class SuveillanceSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    valid_until = models.DateTimeField(default=get_thirty_minutes_from_now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.id)
