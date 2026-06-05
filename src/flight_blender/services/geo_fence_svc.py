from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from decimal import Decimal
from functools import partial
from typing import TYPE_CHECKING, Any

import arrow
import pyproj
from implicitdict import ImplicitDict
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform, unary_union

from flight_blender.domain_types.geo_fence import (
    ED269Geometry,
    GeoAwarenessImportResponseEnum,
    GeoAwarenessStatusResponseEnum,
    GeoAwarenessTestStatus,
    GeoSpatialMapTestHarnessStatus,
    GeoZoneCheckRequestBody,
    GeoZoneCheckResult,
    GeozoneCheckResultEnum,
    GeoZoneChecksResponse,
    GeoZoneFeature,
    GeoZoneFilterPosition,
    HorizontalProjection,
    ParseValidateResponse,
    ZoneAuthority,
)
from flight_blender.repositories.geo_fence_repo import SQLAlchemyGeoFenceRepository
from flight_blender.utils.spatial_geo_fence import RTreeGeoFenceSpatialService

if TYPE_CHECKING:
    from flight_blender.tasks.geo_fence_task import CeleryGeoFenceTaskDispatcher

proj_wgs84 = pyproj.Proj("+proj=longlat +datum=WGS84")


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
    def __init__(
        self,
        repo: SQLAlchemyGeoFenceRepository,
        dispatcher: CeleryGeoFenceTaskDispatcher,
        spatial: RTreeGeoFenceSpatialService,
        redis: Any,
    ):
        self.repo = repo
        self.dispatcher = dispatcher
        self.spatial = spatial
        self.redis: Any = redis

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
            fences = self.spatial.filter_fences_by_viewport(fences=fences, viewport=view_port)

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
        key = "geoawarenes_test." + geozone_source_id
        if await self.redis.exists(key):
            return json.loads(await self.redis.get(key))
        return None

    async def put_geozone_source(self, geozone_source_id: str, geo_zone_url: str) -> dict:
        key = "geoawarenes_test." + geozone_source_id
        response = GeoAwarenessTestStatus(result=GeoAwarenessImportResponseEnum.Activating, message="")
        self.dispatcher.download_geozone_source(geo_zone_url=geo_zone_url, geozone_source_id=geozone_source_id)
        await self.redis.set(key, json.dumps(asdict(response)))
        await self.redis.expire(name=key, time=3000)
        return asdict(response)

    async def delete_geozone_source(self, geozone_source_id: str) -> dict | None:
        key = "geoawarenes_test." + geozone_source_id
        if not await self.redis.exists(key):
            return None
        await self.repo.delete_test_geofences()
        deletion_status = GeoAwarenessTestStatus(
            result=GeoAwarenessImportResponseEnum.Deactivating,
            message="Test data has been scheduled to be deleted",
        )
        await self.redis.set(key, json.dumps(asdict(deletion_status)))
        return asdict(deletion_status)

    async def check_geozones(self, body: GeoZoneCheckRequestBody) -> dict:
        geo_zones_of_interest = False

        test_fences = await self.repo.get_test_geofences()

        for geo_zone_check in body.checks:
            for filter_set in geo_zone_check["filter_sets"]:
                if "position" in filter_set and filter_set["position"]:
                    filter_position = GeoZoneFilterPosition(**filter_set["position"])
                    if self.spatial.has_intersection_at_position(
                        fences=test_fences,
                        longitude=filter_position.longitude,
                        latitude=filter_position.latitude,
                    ):
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


class GeoZoneParser:
    def __init__(self, geo_zone):
        self.geo_zone = geo_zone

    def parse_validate_geozone(self) -> ParseValidateResponse:
        processed_geo_zone_features: list[GeoZoneFeature] = []
        all_zones_valid: list[bool] = []
        for _geo_zone_feature in self.geo_zone["features"]:
            zone_authorities = _geo_zone_feature["zoneAuthority"]
            all_zone_authorities = []
            for z_a in zone_authorities:
                zone_authority = ImplicitDict.parse(z_a, ZoneAuthority)
                all_zone_authorities.append(zone_authority)
            ed_269_geometries = []

            all_ed_269_geometries = _geo_zone_feature["geometry"]

            for ed_269_geometry in all_ed_269_geometries:
                parse_error = False
                if ed_269_geometry["horizontalProjection"]["type"] == "Polygon":
                    pass
                elif ed_269_geometry["horizontalProjection"]["type"] == "Circle":
                    try:
                        lat = ed_269_geometry["horizontalProjection"]["center"][1]
                        lng = ed_269_geometry["horizontalProjection"]["center"][0]
                        radius = ed_269_geometry["horizontalProjection"]["radius"]
                    except KeyError as ke:
                        from loguru import logger

                        logger.info("Error in parsing points provided in the ED 269 file %s" % ke)
                        parse_error = True
                    else:
                        r = radius / 1000
                        buf = geodesic_point_buffer(lat, lng, r)
                        b = mapping(buf)
                        fc = {
                            "type": "FeatureCollection",
                            "features": [{"type": "Feature", "properties": {}, "geometry": b}],
                        }
                        from loguru import logger

                        logger.info("Converting point to circle")
                        ed_269_geometry["horizontalProjection"] = b
                if not parse_error:
                    horizontal_projection = ImplicitDict.parse(ed_269_geometry["horizontalProjection"], HorizontalProjection)
                    parse_error = False
                    ed_269_geometry = ED269Geometry(
                        uomDimensions=ed_269_geometry["uomDimensions"],
                        lowerLimit=ed_269_geometry["lowerLimit"],
                        lowerVerticalReference=ed_269_geometry["lowerVerticalReference"],
                        upperLimit=ed_269_geometry["upperLimit"],
                        upperVerticalReference=ed_269_geometry["upperVerticalReference"],
                        horizontalProjection=horizontal_projection,
                    )
                    ed_269_geometries.append(ed_269_geometry)

            geo_zone_feature = GeoZoneFeature(
                identifier=_geo_zone_feature["identifier"],
                country=_geo_zone_feature["country"],
                name=_geo_zone_feature["name"],
                type=_geo_zone_feature["type"],
                restriction=_geo_zone_feature["restriction"],
                restrictionConditions=_geo_zone_feature["restrictionConditions"],
                region=_geo_zone_feature["region"],
                reason=_geo_zone_feature["reason"],
                otherReasonInfo=_geo_zone_feature["otherReasonInfo"],
                regulationExemption=_geo_zone_feature["regulationExemption"],
                uSpaceClass=_geo_zone_feature["uSpaceClass"],
                message=_geo_zone_feature["message"],
                applicability=_geo_zone_feature["applicability"],
                zoneAuthority=all_zone_authorities,
                geometry=ed_269_geometries,
            )
            processed_geo_zone_features.append(geo_zone_feature)
            all_zones_valid.append(True)

        return ParseValidateResponse(all_zones=all_zones_valid, feature_list=processed_geo_zone_features)


def geodesic_point_buffer(lat, lon, km):
    aeqd_proj = "+proj=aeqd +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0"
    project = partial(pyproj.transform, pyproj.Proj(aeqd_proj.format(lat=lat, lon=lon)), proj_wgs84)
    buf = Point(0, 0).buffer(km * 1000)
    return transform(project, buf)


def validate_geo_zone(geo_zone) -> bool:
    if all(k in geo_zone for k in ("title", "description", "features")):
        pass
    else:
        return False

    my_geo_zone_parser = GeoZoneParser(geo_zone=geo_zone)
    parse_response = my_geo_zone_parser.parse_validate_geozone()

    all_zones = parse_response.all_zones
    all_zones_valid = all(all_zones)
    return all_zones_valid
