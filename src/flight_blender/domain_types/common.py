"""Cross-domain shared entity constants.

These constants are referenced by multiple domains (auth, surveillance,
conformance, SCD, etc.) and therefore live in a single shared module rather
than being split across per-domain entity files. Per-domain constants
(``FLIGHT_OBSERVATION_TRAFFIC_SOURCE``, ``SURVEILLANCE_SENSOR_*_CHOICES``)
remain in their respective ``core/entities/<domain>.py`` files.
"""

from flight_blender.config import settings


def _(s):
    return s


FLIGHTBLENDER_READ_SCOPE = settings.FLIGHTBLENDER_READ_SCOPE

FLIGHTBLENDER_WRITE_SCOPE = settings.FLIGHTBLENDER_WRITE_SCOPE


ALTITUDE_REF = (
    (0, _("WGS84")),
    (1, _("AGL")),
    (2, _("MSL")),
    (4, _("W84")),
)
ALTITUDE_REF_LOOKUP = {
    "WGS84": 0,
    "AGL": 1,
    "MSL": 2,
    "W84": 4,
}
CONFORMANCE_STATES = (
    (0, _("Nonconforming")),
    (1, _("Conforming")),
)
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

USS_AVAILABILITY = (
    (0, _("Unknown")),
    (1, _("Normal")),
    (2, _("Down")),
)

# When an operator changes a state, he / she puts a new state (via the API), this object specifies the event when a operator takes action
OPERATOR_EVENT_LOOKUP = {
    5: "operator_confirms_ended",
    2: "operator_activates",
    4: "operator_initiates_contingent",
    6: "operator_withdraws",
    7: "operator_cancels",
}

VALID_OPERATIONAL_INTENT_STATES = [
    "Accepted",
    "Activated",
    "Nonconforming",
    "Contingent",
]


RESPONSE_CONTENT_TYPE = "application/json"

# Locations for Index Creation, using tmp to avoid permission issues in Docker / Kubernetes
FLIGHT_DECLARATION_INDEX_BASEPATH = "/tmp/blender_flight_declaration_idx"  # nosec B108
FLIGHT_DECLARATION_OPINT_INDEX_BASEPATH = "/tmp/blender_opint_idx"  # nosec B108
GEOFENCE_INDEX_BASEPATH = "/tmp/blender_geofence_idx"  # nosec B108
OPINT_INDEX_BASEPATH = "/tmp/blender_opint_proc_idx"  # nosec B108
