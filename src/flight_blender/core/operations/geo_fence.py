import json
import uuid
from dataclasses import asdict
from decimal import Decimal
from typing import Any

import arrow
import pyproj
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from flight_blender.auth.common import get_async_redis
from flight_blender.common.data_definitions import GEOFENCE_INDEX_BASEPATH
from flight_blender.geo_fence import rtree_geo_fence_helper
from flight_blender.geo_fence.buffer_helper import toFromUTM
from flight_blender.geo_fence.data_definitions import (
    GeoAwarenessImportResponseEnum,
    GeoAwarenessStatusResponseEnum,
    GeoAwarenessTestStatus,
    GeoSpatialMapTestHarnessStatus,
    GeoZoneCheckRequestBody,
    GeoZoneCheckResult,
    GeozoneCheckResultEnum,
    GeoZoneChecksResponse,
    GeoZoneFilterPosition,
)
from flight_blender.geo_fence.tasks import download_geozone_source
from flight_blender.infrastructure.database.repositories.sa_geo_fence import SQLAlchemyGeoFenceRepository


def _compute_bounds_and_times(features: list[dict]) -> tuple[str, str, str, Decimal, Decimal, str]:
    """Extract bounds, times, limits, and name from a GeoJSON feature list."""
    shp_features = [shape(f["geometry"]) for f in features]
    combined = unary_union(shp_features)
    bounds = ",".join([f"{x:.7f}" for x in combined.bounds])

    last_feature = features[-1]
    props = last_feature.get("properties", {})
    start_time = arrow.now().isoformat() if "start_time" not in props else arrow.get(props["start_time"]).isoformat()
    end_time = arrow.now().shift(hours=1).isoformat() if "end_time" not in props else arrow.get(props["end_time"]).isoformat()
    upper_limit = Decimal(str(props.get("upper_limit", 100)))
    lower_limit = Decimal(str(props.get("lower_limit", 0)))
    name = props.get("name", "")
    return bounds, start_time, end_time, upper_limit, lower_limit, name


class GeoFenceOperations:
    def __init__(self, repo: SQLAlchemyGeoFenceRepository):
        self.repo = repo

    async def list_geofences(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        viewport: str | None = None,
    ) -> list[dict]:
        present = arrow.now()
        if start_date and end_date:
            s_date = arrow.get(start_date, "YYYY-MM-DD")
            e_date = arrow.get(end_date, "YYYY-MM-DD")
        else:
            s_date = present.shift(days=-1)
            e_date = present.shift(days=1)

        fences = await self.repo.get_geofences_by_date_range(
            start=s_date.datetime,
            end=e_date.datetime,
            is_test=False,
        )

        if viewport:
            view_port = [float(x) for x in viewport.split(",")]
            my_rtree = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
            my_rtree.generate_geo_fence_index(all_fences=fences)
            relevant = my_rtree.check_box_intersection(view_box=view_port)
            my_rtree.clear_rtree_index(all_fences=fences)
            relevant_ids = {r["geo_fence_id"] for r in relevant}
            fences = [f for f in fences if str(f.id) in relevant_ids]

        return [_fence_to_dict(f) for f in fences]

    async def get_geofence(self, geofence_id: uuid.UUID) -> dict | None:
        fence = await self.repo.get_by_id(geofence_id)
        if fence is None:
            return None
        return _fence_to_dict(fence)

    async def create_geofence_from_feature_collection(self, geo_fence_data: dict) -> dict:
        features = geo_fence_data["features"]
        if not features:
            raise ValueError("features list must not be empty")
        bounds, start_time, end_time, upper_limit, lower_limit, name = _compute_bounds_and_times(features)
        raw_geo_fence = json.dumps(geo_fence_data)

        fence = await self.repo.create(
            raw_geo_fence=raw_geo_fence,
            start_datetime=arrow.get(start_time).datetime,
            end_datetime=arrow.get(end_time).datetime,
            upper_limit=upper_limit,
            lower_limit=lower_limit,
            bounds=bounds,
            name=name,
        )
        return {"message": "Geofence Declaration submitted", "id": str(fence.id)}

    async def delete_geofence(self, geofence_id: uuid.UUID) -> bool:
        return await self.repo.delete(geofence_id)

    async def get_geospatial_data_sources(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        viewport: str | None = None,
    ) -> list[dict]:
        present = arrow.now()
        if start_date and end_date:
            s_date = arrow.get(start_date, "YYYY-MM-DD")
            e_date = arrow.get(end_date, "YYYY-MM-DD")
        else:
            s_date = present.shift(days=-1)
            e_date = present.shift(days=1)

        fences = await self.repo.get_geospatial_data_sources(
            start=s_date.datetime,
            end=e_date.datetime,
        )
        return [_fence_to_dict(f) for f in fences]

    async def get_test_harness_status(self) -> dict:
        result = GeoSpatialMapTestHarnessStatus(
            status=GeoAwarenessStatusResponseEnum.Ready,
            api_version="latest",
        )
        return asdict(result)

    async def get_geozone_source_status(self, geozone_source_id: str) -> dict | None:
        r = get_async_redis()
        key = "geoawarenes_test." + geozone_source_id
        if await r.exists(key):
            return json.loads(await r.get(key))
        return None

    async def put_geozone_source(self, geozone_source_id: str, geo_zone_url: str) -> dict:
        r = get_async_redis()
        key = "geoawarenes_test." + geozone_source_id
        response = GeoAwarenessTestStatus(result=GeoAwarenessImportResponseEnum.Activating, message="")
        download_geozone_source.delay(geo_zone_url=geo_zone_url, geozone_source_id=geozone_source_id)
        await r.set(key, json.dumps(asdict(response)))
        await r.expire(name=key, time=3000)
        return asdict(response)

    async def delete_geozone_source(self, geozone_source_id: str) -> dict | None:
        r = get_async_redis()
        key = "geoawarenes_test." + geozone_source_id
        if not await r.exists(key):
            return None
        await self.repo.delete_test_geofences()
        deletion_status = GeoAwarenessTestStatus(
            result=GeoAwarenessImportResponseEnum.Deactivating,
            message="Test data has been scheduled to be deleted",
        )
        await r.set(key, json.dumps(asdict(deletion_status)))
        return asdict(deletion_status)

    async def check_geozones(self, body: GeoZoneCheckRequestBody) -> dict:
        proj = pyproj.Proj("+proj=utm +zone=24 +south +datum=WGS84 +units=m +no_defs ")
        geo_zones_of_interest = False

        test_fences = await self.repo.get_test_geofences()

        for geo_zone_check in body.checks:
            for filter_set in geo_zone_check["filter_sets"]:
                if "position" in filter_set and filter_set["position"]:
                    filter_position = GeoZoneFilterPosition(**filter_set["position"])
                    my_rtree = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
                    init_point = Point(filter_position.longitude, filter_position.latitude)
                    init_shape_utm = toFromUTM(init_point, proj)
                    buffer_shape_utm = init_shape_utm.buffer(1)
                    buffer_shape_lonlat = toFromUTM(buffer_shape_utm, proj, inv=True)
                    view_port = buffer_shape_lonlat.bounds
                    my_rtree.generate_geo_fence_index(all_fences=test_fences)
                    relevant = my_rtree.check_box_intersection(view_box=view_port)
                    my_rtree.clear_rtree_index(all_fences=test_fences)
                    if relevant:
                        geo_zones_of_interest = True

                if "after" in filter_set and filter_set["after"]:
                    after_dt = arrow.get(filter_set["after"]).datetime
                    if any(f.end_datetime > after_dt for f in test_fences):
                        geo_zones_of_interest = True

                if "before" in filter_set and filter_set["before"]:
                    before_dt = arrow.get(filter_set["before"]).datetime
                    if any(f.start_datetime < before_dt for f in test_fences):
                        geo_zones_of_interest = True

        if geo_zones_of_interest:
            result = GeoZoneCheckResult(geozone=GeozoneCheckResultEnum.Present)
        else:
            result = GeoZoneCheckResult(geozone=GeozoneCheckResultEnum.Absent)

        response = GeoZoneChecksResponse(applicableGeozone=[result], message="Test")
        return asdict(response)


def _fence_to_dict(fence: Any) -> dict:
    return {
        "id": str(fence.id),
        "raw_geo_fence": fence.raw_geo_fence,
        "geozone": fence.geozone,
        "upper_limit": str(fence.upper_limit),
        "lower_limit": str(fence.lower_limit),
        "altitude_ref": fence.altitude_ref,
        "name": fence.name,
        "bounds": fence.bounds,
        "status": fence.status,
        "message": fence.message,
        "is_test_dataset": fence.is_test_dataset,
        "start_datetime": fence.start_datetime.isoformat() if fence.start_datetime else None,
        "end_datetime": fence.end_datetime.isoformat() if fence.end_datetime else None,
        "created_at": fence.created_at.isoformat() if fence.created_at else None,
        "updated_at": fence.updated_at.isoformat() if fence.updated_at else None,
    }
