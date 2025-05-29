import uuid
from datetime import datetime

from django.db import models

from flight_declaration_operations.models import FlightDeclaration
from geo_fence_operations.models import GeoFence

# Create your models here.


class ConstraintDetail(models.Model):
    """
    Represents a constraint model used to define operational constraints.
    Attributes:
        id (UUIDField): The unique identifier for the constraint, automatically generated.
        volumes (TextField): A JSON-encoded string representing the volumes associated with the constraint.
        off_nominal_volumes (TextField): A JSON-encoded string representing off-nominal volumes for the constraint.
        priority (IntegerField): The priority level of the constraint, where lower numbers indicate higher priority.
        subscribers (TextField): A JSON-encoded string representing the subscribers associated with the constraint.
        created_at (DateTimeField): The timestamp when the constraint was created, automatically set on creation.
        updated_at (DateTimeField): The timestamp when the constraint was last updated, automatically updated on save.
    Meta:
        ordering (list): Specifies the default ordering of constraints by creation date in descending order.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geofence = models.OneToOneField(
        GeoFence,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Reference to the geofence associated with the constraint.",
    )
    volumes = models.TextField(blank=True)
    _type = models.CharField(max_length=256, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class ConstraintReference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flight_declaration = models.ForeignKey(
        FlightDeclaration,
        on_delete=models.CASCADE,
        help_text="Reference to the flight declaration associated with the constraint.",
        null=True,
        blank=True,
    )
    geofence = models.OneToOneField(
        GeoFence,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Reference to the geofence associated with the constraint.",
    )
    uss_availability = models.CharField(max_length=40, blank=True)

    ovn = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="Once the operational intent is created, the OVN is stored here.",
    )

    manager = models.CharField(
        max_length=256,
        null=True,
    )
    uss_base_url = models.CharField(
        max_length=256,
        help_text="USS base URL",
        blank=True,
    )
    version = models.CharField(max_length=256, help_text="Constraint version", blank=True)
    time_start = models.DateTimeField(default=datetime.now)
    time_end = models.DateTimeField(default=datetime.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_live = models.BooleanField(
        default=False,
        help_text="Set to true if the operational intent is live",
    )

    class Meta:
        ordering = ["-created_at"]


class CompositeConstraint(models.Model):
    """
    CompositeConstraint model links a constraint reference with constraint details.
    It associates a specific FlightDeclaration with defined spatial and temporal bounds,
    altitude limits, and references to both the constraint's metadata and its detailed description.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    declaration = models.ForeignKey(FlightDeclaration, on_delete=models.CASCADE)
    bounds = models.CharField(max_length=140)
    start_datetime = models.DateTimeField(default=datetime.now)
    end_datetime = models.DateTimeField(default=datetime.now)
    alt_max = models.FloatField()
    alt_min = models.FloatField()
    constraint_reference = models.ForeignKey(ConstraintReference, on_delete=models.CASCADE, related_name="composite_constraint_reference")
    constraint_detail = models.ForeignKey(ConstraintDetail, on_delete=models.CASCADE, related_name="composite_constraint_detail")
