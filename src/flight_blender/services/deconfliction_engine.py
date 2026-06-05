"""Built-in RTree bounding-box de-confliction engine."""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.domain_types.common import FLIGHT_DECLARATION_INDEX_BASEPATH, GEOFENCE_INDEX_BASEPATH
from flight_blender.domain_types.flight_declarations import DeconflictionRequest, DeconflictionResult
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.repositories.geo_fence_repo import SQLAlchemyGeoFenceRepository
from flight_blender.utils import spatial_geo_fence as rtree_geo_fence_helper
from flight_blender.utils.spatial_flight_declarations import FlightDeclarationRTreeIndexFactory


class DefaultDeconflictionEngine:
    """Built-in RTree bounding-box de-confliction engine.

    1. Check geofence bbox conflicts (RTree).
    2. Check active flight declaration bbox conflicts (RTree).
    3. Any intersection → rejected (state 8).
    """

    async def check_deconfliction(self, request: DeconflictionRequest, db: AsyncSession) -> DeconflictionResult:
        view_box = request.view_box
        start_datetime = request.start_datetime
        end_datetime = request.end_datetime
        ussp_network_enabled = request.ussp_network_enabled

        all_relevant_fences: list = []
        all_relevant_declarations: list = []
        is_approved = True
        declaration_state = 0 if ussp_network_enabled else 1

        fence_repo = SQLAlchemyGeoFenceRepository(db)
        all_fences = await fence_repo.get_geofences_overlapping_time_window(
            start=start_datetime,
            end=end_datetime,
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

        fd_repo = SQLAlchemyFlightDeclarationRepository(db)
        current_declaration_id = request.declaration_id
        exclude_id = uuid.UUID(str(current_declaration_id)) if current_declaration_id is not None else None
        declaration_list = await fd_repo.get_active_declarations_overlapping_time_window(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            exclude_declaration_id=exclude_id,
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
                asyncio.run(fd_rtree_helper.clear_rtree_index(declaration_list))

        return DeconflictionResult(
            all_relevant_fences=all_relevant_fences,
            all_relevant_declarations=all_relevant_declarations,
            is_approved=is_approved,
            declaration_state=declaration_state,
        )
