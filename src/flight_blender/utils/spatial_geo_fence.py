import hashlib
import json
import os

import arrow
import pyproj
import shapely.geometry as shp_geo
from loguru import logger
from rtree import index
from rtree.exceptions import RTreeError
from shapely.geometry import Point
from shapely.geometry import Polygon as ShpPolygon

from flight_blender.domain_types.geo_fence import GEOFENCE_INDEX_BASEPATH, GeoFenceMetadata
from flight_blender.auth.token_cache import get_redis

# ── buffer helpers (from geo_fence/buffer_helper.py) ─────────────────────────


def toFromUTM(shp, proj, inv=False):
    geoInterface = shp.__geo_interface__
    shpType = geoInterface["type"]
    coords = geoInterface["coordinates"]

    if shpType == "Polygon":
        newCoord = [[proj(*point, inverse=inv) for point in linring] for linring in coords]
    elif shpType == "MultiPolygon":
        newCoord = [[[proj(*point, inverse=inv) for point in linring] for linring in poly] for poly in coords]
    elif shpType == "LineString":
        newCoord = [proj(*point, inverse=inv) for point in coords]
    elif shpType == "Point":
        newCoord = proj(*coords, inverse=inv)

    return shp_geo.shape({"type": shpType, "coordinates": tuple(newCoord)})


def convert_shapely_to_geojson(shp: ShpPolygon) -> str:
    shp_polygon = shp_geo.mapping(shp)
    return json.dumps(shp_polygon)


# ── rtree index helpers (from geo_fence/rtree_geo_fence_helper.py) ────────────


def _open_or_recover_index(base_path: str) -> index.Index:
    try:
        return index.Index(base_path)
    except RTreeError:
        logger.warning("Corrupt RTree index at {}, recreating", base_path)
        for ext in (".idx", ".dat"):
            path = base_path + ext
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    logger.exception("Failed to remove corrupt RTree index file {} during recovery", path)
        return index.Index(base_path)


class GeoFenceRTreeIndexFactory:
    def __init__(self, index_name: str):
        self.idx = _open_or_recover_index(index_name)
        self.r = get_redis()

    def add_box_to_index(self, id: int, geo_fence_id: str, view: list[float], start_date: str, end_date: str):
        from dataclasses import asdict

        metadata = GeoFenceMetadata(start_date=start_date, end_date=end_date, geo_fence_id=geo_fence_id)
        self.idx.insert(id=id, coordinates=(view[0], view[1], view[2], view[3]), obj=asdict(metadata))

    def delete_from_index(self, enumerated_id: int, view: list[float]):
        self.idx.delete(id=enumerated_id, coordinates=(view[0], view[1], view[2], view[3]))

    def generate_geo_fence_index(self, all_fences) -> None:
        present = arrow.now()
        start_date = present.shift(days=-1).isoformat()
        end_date = present.shift(days=1).isoformat()

        for fence in all_fences:
            fence_idx_str = str(fence.id)
            fence_id = int(hashlib.sha256(fence_idx_str.encode("utf-8")).hexdigest(), 16) % 10**8
            view = [float(coord) for coord in fence.bounds.split(",")]
            view = [view[1], view[0], view[3], view[2]]
            self.add_box_to_index(
                id=fence_id,
                geo_fence_id=fence_idx_str,
                view=view,
                start_date=start_date,
                end_date=end_date,
            )

    def clear_rtree_index(self, all_fences) -> None:
        for fence in all_fences:
            fence_idx_str = str(fence.id)
            fence_id = int(hashlib.sha256(fence_idx_str.encode("utf-8")).hexdigest(), 16) % 10**8
            view = [float(coord) for coord in fence.bounds.split(",")]
            self.delete_from_index(enumerated_id=fence_id, view=view)

    def check_box_intersection(self, view_box: list[float]):
        intersections = [n.object for n in self.idx.intersection((view_box[0], view_box[1], view_box[2], view_box[3]), objects=True)]
        return intersections


# ── spatial service ───────────────────────────────────────────────────────────


class RTreeGeoFenceSpatialService:
    def filter_fences_by_viewport(self, fences: list, viewport: list[float]) -> list:
        my_rtree = GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
        my_rtree.generate_geo_fence_index(all_fences=fences)
        relevant = my_rtree.check_box_intersection(view_box=viewport)
        my_rtree.clear_rtree_index(all_fences=fences)
        relevant_ids = {r["geo_fence_id"] for r in relevant}
        return [f for f in fences if str(f.id) in relevant_ids]

    def has_intersection_at_position(self, fences: list, longitude: float, latitude: float) -> bool:
        proj = pyproj.Proj("+proj=utm +zone=24 +south +datum=WGS84 +units=m +no_defs ")
        init_point = Point(longitude, latitude)
        init_shape_utm = toFromUTM(init_point, proj)
        buffer_shape_utm = init_shape_utm.buffer(1)
        buffer_shape_lonlat = toFromUTM(buffer_shape_utm, proj, inv=True)
        view_port = buffer_shape_lonlat.bounds

        my_rtree = GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
        my_rtree.generate_geo_fence_index(all_fences=fences)
        relevant = my_rtree.check_box_intersection(view_box=view_port)
        my_rtree.clear_rtree_index(all_fences=fences)
        return bool(relevant)
