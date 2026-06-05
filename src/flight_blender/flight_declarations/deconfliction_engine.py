"""Built-in RTree bounding-box de-confliction engine.

Replicates the original ``check_intersections`` logic that was previously
inlined in ``FlightDeclarationRequestValidator``:

1. Check geofence bbox conflicts (RTree).
2. Check active flight declaration bbox conflicts (RTree).
3. Any intersection → rejected (state 8).

This class satisfies :class:`~flight_blender.flight_declarations.deconfliction_protocol.DeconflictionEngine`
without inheriting from it (structural subtyping).
"""

import uuid

from sqlalchemy import select

from flight_blender.common.data_definitions import ACTIVE_OPERATIONAL_STATES, FLIGHT_DECLARATION_INDEX_BASEPATH, GEOFENCE_INDEX_BASEPATH
from flight_blender.flight_declarations.data_definitions import DeconflictionRequest, DeconflictionResult
from flight_blender.infrastructure.spatial.flight_declarations import FlightDeclarationRTreeIndexFactory
from flight_blender.geo_fence import rtree_geo_fence_helper
from flight_blender.infrastructure.database.models.flight_declarations import FlightDeclarationORM
from flight_blender.infrastructure.database.models.geo_fence import GeoFenceORM
from flight_blender.infrastructure.database.session import session_scope


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
        with session_scope() as db:
            all_fences = list(
                db.execute(
                    select(GeoFenceORM).where(
                        GeoFenceORM.start_datetime <= start_datetime,
                        GeoFenceORM.end_datetime >= end_datetime,
                    )
                )
                .scalars()
                .all()
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
                    geo_fence_index.clear_rtree_index(all_fences=all_fences)

        # ── Flight declaration intersection ──────────────────────────────
        with session_scope() as db:
            stmt = select(FlightDeclarationORM).where(
                FlightDeclarationORM.state.in_(ACTIVE_OPERATIONAL_STATES),
                FlightDeclarationORM.start_datetime <= end_datetime,
                FlightDeclarationORM.end_datetime >= start_datetime,
            )
            current_declaration_id = request.declaration_id
            if current_declaration_id is not None:
                stmt = stmt.where(FlightDeclarationORM.id != uuid.UUID(str(current_declaration_id)))
            declaration_list = list(db.execute(stmt).scalars().all())

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
