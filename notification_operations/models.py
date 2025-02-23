# Create your models here.
# Create your models here.

import uuid

from django.db import models

# Create your models here.


class OperatorRIDNotification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.TextField(help_text="Specify the message to be sent to the operator")
    is_active = models.BooleanField(
        default=True,
        help_text="Specify if the notification is active, only active notifications will be sent to the operator",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
