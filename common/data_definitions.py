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


FLIGHT_OPINT_KEY = "flight_opint."
RESPONSE_CONTENT_TYPE = "application/json"
