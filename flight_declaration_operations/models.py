import itertools
import uuid
from datetime import datetime
from typing import List

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.data_definitions import OPERATION_STATES, OPERATION_TYPES


class FlightDeclaration(models.Model):
    """
    FlightDeclaration model represents a flight declaration with various attributes and methods to manage its state and history.
    Attributes:
        id (UUIDField): Primary key, unique identifier for the flight declaration.
        operational_intent (TextField): Description of the operational intent.
        flight_declaration_raw_geojson (TextField): Raw GeoJSON data for the flight declaration, optional.
        type_of_operation (IntegerField): Type of operation, choices are VLOS and BVLOS.
        bounds (CharField): Geographical bounds of the operation.
        aircraft_id (CharField): ID of the aircraft for this declaration.
        state (IntegerField): Current state of the operation.
        originating_party (CharField): Party originating the flight.
        submitted_by (EmailField): Email of the person who submitted the declaration, optional.
        approved_by (EmailField): Email of the person who approved the declaration, optional.
        latest_telemetry_datetime (DateTimeField): Timestamp of the last received telemetry for this operation, optional.
        start_datetime (DateTimeField): Start time of the operation.
        end_datetime (DateTimeField): End time of the operation.
        is_approved (BooleanField): Approval status of the flight declaration.
        created_at (DateTimeField): Timestamp when the declaration was created.
        updated_at (DateTimeField): Timestamp when the declaration was last updated.
    Methods:
        add_state_history_entry(original_state: int, new_state: int, notes: str = "", **kwargs):
            Adds a history tracking entry for this FlightDeclaration.
        get_state_history() -> List[int]:
            Retrieves the state history of the flight declaration and parses it to build a transition list.
        __unicode__():
            Returns a string representation of the flight declaration for unicode support.
        __str__():
            Returns a string representation of the flight declaration.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operational_intent = models.TextField()
    flight_declaration_raw_geojson = models.TextField(null=True, blank=True)
    type_of_operation = models.IntegerField(
        choices=OPERATION_TYPES,
        default=1,
        help_text="At the moment, only Visual Line of Sight (VLOS) and Beyond Visual Line of Sight (BVLOS) operations are supported, for other types of operations, please issue a pull-request",
    )
    bounds = models.CharField(max_length=140)
    aircraft_id = models.CharField(
        max_length=256,
        help_text="Specify the ID of the aircraft for this declaration",
    )
    state = models.IntegerField(choices=OPERATION_STATES, default=0, help_text="Set the state of operation")

    originating_party = models.CharField(
        max_length=100,
        help_text="Set the party originating this flight, you can add details e.g. Aerobridge Flight 105",
        default="Flight Blender Default",
    )

    submitted_by = models.EmailField(blank=True, null=True)
    approved_by = models.EmailField(blank=True, null=True)

    latest_telemetry_datetime = models.DateTimeField(
        help_text="The time at which the last telemetry was received for this operation, this is used to determine operational conformance",
        blank=True,
        null=True,
    )

    start_datetime = models.DateTimeField(default=datetime.now)
    end_datetime = models.DateTimeField(default=datetime.now)

    is_approved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def add_state_history_entry(self, original_state: int, new_state: int, notes: str = "", **kwargs):
        """Add a history tracking entry for this FlightDeclaration.
        Args:
            user (User): The user performing this action # Not implemented
            notes (str, optional): URL associated with this tracking entry. Defaults to ''.
        """

        original_state = original_state or "start"
        deltas = {"original_state": str(original_state), "new_state": str(new_state)}

        entry = FlightOperationTracking.objects.create(
            flight_declaration=self,
            notes=notes,
            deltas=deltas,
        )

        entry.save()

    def get_state_history(self) -> List[int]:
        """
        This method gets the state history of a flight declaration and then parses it to build a transition
        """
        all_states = []
        historic_states = FlightOperationTracking.objects.filter(flight_declaration=self).order_by("created_at")
        for historic_state in historic_states:
            delta = historic_state.deltas
            original_state = delta.get("original_state", "start")
            new_state = delta.get("new_state", "start")
            if original_state == "start":
                original_state = -1
            all_states.append(int(original_state))
            all_states.append(int(new_state))
        distinct_states = [k for k, g in itertools.groupby(all_states)]
        return distinct_states

    def __unicode__(self):
        return self.originating_party + " " + str(self.id)

    def __str__(self):
        return self.originating_party + " " + str(self.id)


class FlightOperationalIntentDetail(models.Model):
    """
    Model representing a flight authorization.
    Attributes:
        id (UUIDField): Primary key, unique identifier for the flight authorization.
        declaration (OneToOneField): One-to-one relationship with FlightDeclaration, deleted on cascade.
        dss_operational_intent_reference_id (CharField): Operational intent ID shared on the DSS, optional.
        created_at (DateTimeField): Timestamp when the flight authorization was created, auto-generated.
        updated_at (DateTimeField): Timestamp when the flight authorization was last updated, auto-generated.
    Methods:
        __unicode__(): Returns a string representation of the flight authorization.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    declaration = models.OneToOneField(FlightDeclaration, on_delete=models.CASCADE)

    volumes = models.TextField(blank=True)
    off_nominal_volumes = models.TextField(blank=True)
    priority = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_live = models.BooleanField(
        default=False,
        help_text="Set to true if the operational intent is live",
    )

    class Meta:
        ordering = ["-created_at"]


class FlightOperationalIntentReference(models.Model):
    """
    Model representing a flight authorization.
    Attributes:
        id (UUIDField): Primary key, unique identifier for the flight authorization.
        declaration (OneToOneField): One-to-one relationship with FlightDeclaration, deleted on cascade.
        dss_operational_intent_reference_id (CharField): Operational intent ID shared on the DSS, optional.
        created_at (DateTimeField): Timestamp when the flight authorization was created, auto-generated.
        updated_at (DateTimeField): Timestamp when the flight authorization was last updated, auto-generated.
    Methods:
        __unicode__(): Returns a string representation of the flight authorization.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    declaration = models.OneToOneField(FlightDeclaration, on_delete=models.CASCADE)
    uss_availability = models.CharField(max_length=256)

    ovn = models.CharField(
        max_length=36,
        blank=True,
        null=True,
        help_text="Once the operational intent is created, the OVN is stored here.",
    )

    manager = models.CharField(
        max_length=256,
    )
    uss_base_url = models.CharField(
        max_length=256,
        help_text="USS base URL",
    )
    version = models.CharField(max_length=256, help_text="USS base URL")
    state = models.CharField(max_length=40)
    time_start = models.DateTimeField(default=datetime.now)
    time_end = models.DateTimeField(default=datetime.now)
    subscription_id = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_live = models.BooleanField(
        default=False,
        help_text="Set to true if the operational intent is live",
    )

    class Meta:
        ordering = ["-created_at"]


class CompositeOperationalIntent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    declaration = models.OneToOneField(FlightDeclaration, on_delete=models.CASCADE)
    bounds = models.CharField(max_length=140)
    start_datetime = models.DateTimeField(default=datetime.now)
    end_datetime = models.DateTimeField(default=datetime.now)
    alt_max = models.FloatField()
    alt_min = models.FloatField()
    operational_intent_details = models.ForeignKey(
        FlightOperationalIntentDetail, on_delete=models.CASCADE, related_name="composite_operational_intent"
    )
    operational_intent_reference = models.ForeignKey(
        FlightOperationalIntentReference, on_delete=models.CASCADE, related_name="composite_operational_intent_reference"
    )


class PeerOperationalIntentDetail(models.Model):
    "Store the details of the operational intent shared by the peer USS"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    volumes = models.TextField(blank=True)
    off_nominal_volumes = models.TextField(blank=True)
    priority = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_live = models.BooleanField(
        default=False,
        help_text="Set to true if the operational intent is live",
    )

    class Meta:
        ordering = ["-created_at"]


class PeerOperationalIntentReference(models.Model):
    "Store the details of the operational intent shared by the peer USS"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    uss_availability = models.CharField(max_length=256)

    ovn = models.CharField(
        max_length=36,
        blank=True,
        null=True,
        help_text="Once the operational intent is created, the OVN is stored here.",
    )

    manager = models.CharField(
        max_length=256,
    )
    uss_base_url = models.CharField(
        max_length=256,
        help_text="USS base URL",
    )
    version = models.CharField(max_length=256, help_text="USS base URL")
    state = models.CharField(max_length=40)
    time_start = models.DateTimeField(default=datetime.now)
    time_end = models.DateTimeField(default=datetime.now)
    subscription_id = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_live = models.BooleanField(
        default=False,
        help_text="Set to true if the operational intent is live",
    )

    class Meta:
        ordering = ["-created_at"]


class PeerCompositeOperationalIntent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    bounds = models.CharField(max_length=140)
    start_datetime = models.DateTimeField(default=datetime.now)
    end_datetime = models.DateTimeField(default=datetime.now)
    alt_max = models.FloatField()
    alt_min = models.FloatField()
    operational_intent_details = models.ForeignKey(
        PeerOperationalIntentDetail, on_delete=models.CASCADE, related_name="peer_composite_operational_intent"
    )
    operational_intent_reference = models.ForeignKey(
        PeerOperationalIntentReference, on_delete=models.CASCADE, related_name="peer_composite_operational_intent_reference"
    )


class FlightOperationTracking(models.Model):
    """
    Model representing the tracking of flight operations.

    Attributes:
        id (UUIDField): Primary key for the tracking entry, automatically generated.
        flight_declaration (ForeignKey): Reference to the associated FlightDeclaration, with cascade delete.
        notes (CharField): Optional notes for the tracking entry, with a maximum length of 512 characters.
        deltas (JSONField): Optional JSON field to store changes or deltas related to the flight operation.
        created_at (DateTimeField): Timestamp of when the tracking entry was created, automatically set.
        updated_at (DateTimeField): Timestamp of the last update to the tracking entry, automatically set.

    Methods:
        __unicode__(): Returns the flight declaration as a string, if available.
        __str__(): Returns the flight declaration as a string, if available.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    """Stock tracking entry - used for tracking history of a particular Flight Declaration. """
    flight_declaration = models.ForeignKey(FlightDeclaration, on_delete=models.CASCADE, related_name="tracking_info")

    notes = models.CharField(
        blank=True,
        null=True,
        max_length=512,
        verbose_name=_("Notes"),
        help_text=_("Entry notes"),
    )

    deltas = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __unicode__(self):
        return self.flight_declaration if self.flight_declaration else ""

    def __str__(self):
        return str(self.flight_declaration) if self.flight_declaration else ""
