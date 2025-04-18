import uuid

from django.db import models

# Create your models here.


class Constraint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    details = models.TextField(
        blank=True,
        null=True,
        help_text="Details of the Constraint.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class ConstraintReference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    details = models.TextField(
        blank=True,
        null=True,
        help_text="Details of the Constraint.",
    )

    ovn = models.CharField(
        max_length=36,
        blank=True,
        null=True,
        help_text="Once the operational intent is created, the OVN is stored here.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
