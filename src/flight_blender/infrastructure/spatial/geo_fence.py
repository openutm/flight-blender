import pyproj
from shapely.geometry import Point

from flight_blender.core.entities.geo_fence import GEOFENCE_INDEX_BASEPATH
from flight_blender.geo_fence import rtree_geo_fence_helper
from flight_blender.geo_fence.buffer_helper import toFromUTM


class RTreeGeoFenceSpatialService:
    def filter_fences_by_viewport(self, fences: list, viewport: list[float]) -> list:
        my_rtree = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
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

        my_rtree = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
        my_rtree.generate_geo_fence_index(all_fences=fences)
        relevant = my_rtree.check_box_intersection(view_box=view_port)
        my_rtree.clear_rtree_index(all_fences=fences)
        return bool(relevant)
