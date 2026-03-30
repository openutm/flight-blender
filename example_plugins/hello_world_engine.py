"""Example: time-window de-confliction engine plugin.

A simple but functional de-confliction engine that rejects flight
declarations whose time windows overlap with existing active
declarations.  Unlike the built-in RTree engine this implementation
does a straightforward database query without spatial indexing —
easy to understand and extend.

To activate, set the environment variable::

    FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=example_plugins.hello_world_engine.HelloWorldEngine

See PLUGINS.md for the full guide.
"""

import logging

from flight_declaration_operations.data_definitions import (
    DeconflictionRequest,
    DeconflictionResult,
)
from flight_declaration_operations.models import FlightDeclaration

logger = logging.getLogger(__name__)

# Operation states — mirrors the constants used by the default engine.
_STATE_ACCEPTED = 0
_STATE_ACCEPTED_WITH_CONDITIONS = 1
_STATE_REJECTED = 8


class HelloWorldEngine:
    """Time-window de-confliction engine.

    Approves a flight declaration only when no existing *accepted*
    declaration overlaps the same time window.  Geofence checks
    are intentionally skipped to keep the example concise.
    """

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        # Find accepted declarations whose time window overlaps the request.
        overlapping = FlightDeclaration.objects.filter(
            start_datetime__lt=request.end_datetime,
            end_datetime__gt=request.start_datetime,
            state__in=[_STATE_ACCEPTED, _STATE_ACCEPTED_WITH_CONDITIONS],
        )

        # Exclude the declaration itself (important for re-evaluation).
        if request.declaration_id:
            overlapping = overlapping.exclude(pk=request.declaration_id)

        conflicting_ids = list(overlapping.values_list("id", flat=True)[:20])
        has_conflicts = len(conflicting_ids) > 0

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
            declaration_state=_STATE_REJECTED if has_conflicts else (
                _STATE_ACCEPTED if request.ussp_network_enabled else _STATE_ACCEPTED_WITH_CONDITIONS
            ),
        )
