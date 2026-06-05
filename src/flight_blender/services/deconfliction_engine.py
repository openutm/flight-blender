"""Built-in RTree bounding-box de-confliction engine."""

import asyncio
import uuid

from sqlalchemy import select

from flight_blender.db.session import session_scope
from flight_blender.domain_types.common import ACTIVE_OPERATIONAL_STATES, FLIGHT_DECLARATION_INDEX_BASEPATH, GEOFENCE_INDEX_BASEPATH
from flight_blender.domain_types.flight_declarations import DeconflictionRequest, DeconflictionResult
from flight_blender.models.flight_declarations_orm import FlightDeclarationORM
from flight_blender.models.geo_fence_orm import GeoFenceORM
from flight_blender.utils import spatial_geo_fence as rtree_geo_fence_helper
from flight_blender.utils.spatial_flight_declarations import FlightDeclarationRTreeIndexFactory


class DefaultDeconflictionEngine:
    """Built-in RTree bounding-box de-confliction engine.

    1. Check geofence bbox conflicts (RTree).
    2. Check active flight declaration bbox conflicts (RTree).
    3. Any intersection → rejected (state 8).
    """

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        view_box = request.view_box
        start_datetime = request.start_datetime
        end_datetime = request.end_datetime
        ussp_network_enabled = request.ussp_network_enabled

        all_relevant_fences: list = []
        all_relevant_declarations: list = []
        is_approved = True
        declaration_state = 0 if ussp_network_enabled else 1

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
                    asyncio.run(fd_rtree_helper.clear_rtree_index(declaration_list))

        return DeconflictionResult(
            all_relevant_fences=all_relevant_fences,
            all_relevant_declarations=all_relevant_declarations,
            is_approved=is_approved,
            declaration_state=declaration_state,
        )
