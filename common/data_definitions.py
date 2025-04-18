from os import environ as env

from django.utils.translation import gettext_lazy as _

FLIGHTBLENDER_READ_SCOPE = env.get("FLIGHTBLENDER_READ_SCOPE", "flightblender.read")

FLIGHTBLENDER_WRITE_SCOPE = env.get("FLIGHTBLENDER_WRITE_SCOPE", "flightblender.write")

OPERATION_STATES = (
    (0, _("Not Submitted")),
    (1, _("Accepted")),
    (2, _("Activated")),
    (3, _("Nonconforming")),
    (4, _("Contingent")),
    (5, _("Ended")),
    (6, _("Withdrawn")),
    (7, _("Cancelled")),
    (8, _("Rejected")),
)
ACTIVE_OPERATIONAL_STATES = [1, 2, 3, 4]

# This is only used int he SCD Test harness therefore it is partial
OPERATION_STATES_LOOKUP = {
    "Accepted": 1,
    "Activated": 2,
}

OPERATION_TYPES = (
    (1, _("VLOS")),
    (2, _("BVLOS")),
    (3, _("CREWED")),
)


# When an operator changes a state, he / she puts a new state (via the API), this object specifies the event when a operator takes action
OPERATOR_EVENT_LOOKUP = {
    5: "operator_confirms_ended",
    2: "operator_activates",
    4: "operator_initiates_contingent",
}

VALID_OPERATIONAL_INTENT_STATES = ["Accepted", "Activated", "Nonconforming", "Contingent"]


RESPONSE_CONTENT_TYPE = "application/json"


FLIGHT_OBSERVATION_TRAFFIC_SOURCE = (
    (0, _("1090ES")),
    (1, _("UAT")),
    (2, _("Multi-radar (MRT)")),
    (3, _("MLAT")),
    (4, _("SSR")),
    (5, _("PSR")),
    (6, _("Mode-S")),
    (7, _("MRT")),
    (8, _("SSR + PSR Fused")),
    (9, _("ADS-B")),
    (10, _("FLARM")),
    (11, _("Network Remote-ID")),
)

# Locations for Index Creation, using tmp to avoid permission issues in Docker / Kubernetes
FLIGHT_DECLARATION_INDEX_BASEPATH = "/tmp/blender_flight_declaration_idx"
FLIGHT_DECLARATION_OPINT_INDEX_BASEPATH = "/tmp/blender_opint_idx"
GEOFENCE_INDEX_BASEPATH = "/tmp/blender_geofence_idx"
OPINT_INDEX_BASEPATH = "/tmp/blender_opint_proc_idx"
