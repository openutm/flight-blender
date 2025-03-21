import uuid

from django.db import models


class ISASubscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription_id = models.UUIDField(help_text="The subscription ID generated by DSS", default=uuid.uuid4, db_index=True)
    view = models.TextField(help_text="The view box of the subscription", blank=True, null=True)
    flight_details = models.TextField(help_text="All the flight details of the subscription", blank=True, null=True)
    end_datetime = models.DateTimeField(help_text="The end datetime of the subscription", blank=True, null=True)
    view_hash = models.IntegerField(help_text="The hash of the view box", blank=True, null=True, db_index=True)
    is_simulated = models.BooleanField(default=False, help_text="Is this for a experimental", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
