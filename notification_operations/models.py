# Create your models here.
# Create your models here.

import uuid

from django.db import models

from flight_declaration_operations.models import FlightDeclaration

# Create your models here.


class OperatorRIDNotification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_id = models.UUIDField(default=uuid.uuid4, blank=True, null=True)
    message = models.TextField(help_text="Specify the message to be sent to the operator")
    is_active = models.BooleanField(
        default=True,
        help_text="Specify if the notification is active, only active notifications will be sent to the operator",
    )
    flight_declaration = models.ForeignKey(FlightDeclaration, blank=True, null=True, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
