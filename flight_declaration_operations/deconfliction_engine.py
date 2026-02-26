"""Built-in RTree bounding-box de-confliction engine.

Replicates the original ``check_intersections`` logic that was previously
inlined in ``FlightDeclarationRequestValidator``:

1. Check geofence bbox conflicts (RTree).
2. Check active flight declaration bbox conflicts (RTree).
3. Any intersection → rejected (state 8).

This class satisfies :class:`~flight_declaration_operations.deconfliction_protocol.DeconflictionEngine`
without inheriting from it (structural subtyping).
"""

from django.db.models import Q

from common.data_definitions import (
    ACTIVE_OPERATIONAL_STATES,
    FLIGHT_DECLARATION_INDEX_BASEPATH,
    GEOFENCE_INDEX_BASEPATH,
)
from flight_declaration_operations.data_definitions import (
    DeconflictionRequest,
    DeconflictionResult,
)
from flight_declaration_operations.flight_declarations_rtree_helper import (
    FlightDeclarationRTreeIndexFactory,
)
from flight_declaration_operations.models import FlightDeclaration
from geo_fence_operations import rtree_geo_fence_helper
from geo_fence_operations.models import GeoFence


class DefaultDeconflictionEngine:
    """Built-in RTree bounding-box de-confliction engine.

    Replicates the original ``check_intersections`` logic:

    1. Check geofence bbox conflicts (RTree).
    2. Check active flight declaration bbox conflicts (RTree).
    3. Any intersection → rejected (state 8).
    """

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        """Evaluate a single flight declaration against existing operations and geofences.

        Args:
            request: All data needed to perform de-confliction.

        Returns:
            A ``DeconflictionResult`` with approval state and conflicting
            entities.
        """
        view_box = request.view_box
        start_datetime = request.start_datetime
        end_datetime = request.end_datetime
        ussp_network_enabled = request.ussp_network_enabled

        all_relevant_fences: list = []
        all_relevant_declarations: list = []
        is_approved = True
        declaration_state = 0 if ussp_network_enabled else 1

        # ── GeoFence spatial check ───────────────────────────────────────
        all_fences = list(
            GeoFence.objects.filter(
                start_datetime__lte=start_datetime,
                end_datetime__gte=end_datetime,
            )
        )

        if all_fences:
            geo_fence_index = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(
                index_name=GEOFENCE_INDEX_BASEPATH,
            )
            try:
                geo_fence_index.generate_geo_fence_index(all_fences=all_fences)
                all_relevant_fences = geo_fence_index.check_box_intersection(view_box=view_box)
                if all_relevant_fences:
                    is_approved = False
                    declaration_state = 8
            finally:
                geo_fence_index.clear_rtree_index()

        # ── Flight declaration intersection ──────────────────────────────
        declaration_list = list(
            FlightDeclaration.objects.filter(
                state__in=ACTIVE_OPERATIONAL_STATES,
                start_datetime__lte=end_datetime,
                end_datetime__gte=start_datetime,
            )
        )

        if declaration_list:
            fd_rtree_helper = FlightDeclarationRTreeIndexFactory(
                index_name=FLIGHT_DECLARATION_INDEX_BASEPATH,
            )
            try:
                fd_rtree_helper.generate_flight_declaration_index(
                    all_flight_declarations=declaration_list,
                )
                all_relevant_declarations = fd_rtree_helper.check_flight_declaration_box_intersection(
                    view_box=view_box,
                )
                if all_relevant_declarations:
                    is_approved = False
                    declaration_state = 8
            finally:
                fd_rtree_helper.clear_rtree_index()

        return DeconflictionResult(
            all_relevant_fences=all_relevant_fences,
            all_relevant_declarations=all_relevant_declarations,
            is_approved=is_approved,
            declaration_state=declaration_state,
        )
