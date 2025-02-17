import itertools
import uuid
from datetime import datetime
from typing import List

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.data_definitions import OPERATION_STATES, OPERATION_TYPES


class FlightDeclaration(models.Model):
    """A flight operation object for permission"""

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

        original_state = original_state if original_state is not None else "start"
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
            original_state = delta["original_state"]
            new_state = delta["new_state"]
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


class FlightAuthorization(models.Model):
    """This object hold the associated Flight Authorization"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    declaration = models.OneToOneField(FlightDeclaration, on_delete=models.CASCADE)
    dss_operational_intent_id = models.CharField(
        max_length=36,
        blank=True,
        null=True,
        help_text="Once the operational intent is shared on the DSS the operational intent is is stored here. By default nothing is stored here.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __unicode__(self):
        return "Authorization for " + self.declaration

    class Meta:
        ordering = ["-created_at"]


class FlightOperationTracking(models.Model):
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
