"""Example: time-window de-confliction engine plugin.

A simple but functional de-confliction engine that rejects flight
declarations whose time windows overlap with existing active
declarations.  Unlike the built-in RTree engine this implementation
does a straightforward database query without spatial indexing —
easy to understand and extend.

To activate, set the environment variable:

.. code-block:: bash

    FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=example_plugins.hello_world_engine.HelloWorldEngine

See PLUGINS.md for the full guide.
"""

from loguru import logger

from common.data_definitions import ACTIVE_OPERATIONAL_STATES, OPERATION_STATES
from flight_declaration_operations.data_definitions import (
    DeconflictionRequest,
    DeconflictionResult,
)
from flight_declaration_operations.models import FlightDeclaration

# Derive state codes directly from the canonical OPERATION_STATES tuple so this
# example can never silently drift out of sync with common/data_definitions.py.
_STATES_LOOKUP = {str(label): code for code, label in OPERATION_STATES}
_STATE_NOT_SUBMITTED = _STATES_LOOKUP["Not Submitted"]  # 0 — pending USSP network validation
_STATE_ACCEPTED = _STATES_LOOKUP["Accepted"]  # 1 — locally accepted (no USSP network)
_STATE_REJECTED = _STATES_LOOKUP["Rejected"]  # 8
del _STATES_LOOKUP  # only needed for initialisation; remove from module namespace

# Reuse the canonical active-states list as an immutable tuple.
_ACTIVE_STATES = tuple(ACTIVE_OPERATIONAL_STATES)  # (1, 2, 3, 4)


class HelloWorldEngine:
    """Time-window de-confliction engine.

    Approves a flight declaration only when no existing *active*
    declaration overlaps the same time window.  Geofence checks
    are intentionally skipped to keep the example concise.
    """

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        # Find active declarations whose time window overlaps the request.
        overlapping = FlightDeclaration.objects.filter(
            start_datetime__lt=request.end_datetime,
            end_datetime__gt=request.start_datetime,
            state__in=_ACTIVE_STATES,
        )

        # Exclude the declaration itself (important for re-evaluation).
        if request.declaration_id:
            overlapping = overlapping.exclude(pk=request.declaration_id)

        conflicting_ids = list(overlapping.values_list("id", flat=True)[:20])
        has_conflicts = bool(conflicting_ids)

        if has_conflicts:
            logger.info(
                "Declaration %s rejected — %d time-window conflict(s)",
                request.declaration_id,
                len(conflicting_ids),
            )
        else:
            logger.info("Declaration %s approved (no time-window conflicts)", request.declaration_id)

        return DeconflictionResult(
            all_relevant_fences=[],
            all_relevant_declarations=conflicting_ids,
            is_approved=not has_conflicts,
            declaration_state=_STATE_REJECTED if has_conflicts else (_STATE_NOT_SUBMITTED if request.ussp_network_enabled else _STATE_ACCEPTED),
        )
