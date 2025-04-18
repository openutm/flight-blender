# Code reference: https://gis.stackexchange.com/a/327046
# Source: https://gist.github.com/harshithjv/bcd3fef5661ce0a2ec20224e8e4ac415
import json

import shapely.geometry as shp_geo
from shapely.geometry import Polygon as ShpPolygon


def toFromUTM(shp, proj, inv=False):
    """
    How to use?
    >>> import shapely.wkt
    >>> import shapely.geometry
    >>> proj = PROJECTION_IN  # constant declared above the function definition
    >>> shp_obj = shapely.wkt.loads("LINESTRING(76.46019279956818 15.335048625850606,76.46207302808762 15.334717526558398)")
    >>> meters = 10
    >>> init_shape_utm = toFromUTM(shp_obj, proj)
    >>> buffer_shape_utm = init_shape_utm.buffer(meters)
    >>> buffer_shape_lonlat = toFromUTM(buffer_shape_utm, proj, inv=True)
    >>> out = shapely.geometry.mapping(buffer_shape_lonlat)
    >>> geojson = json.loads(json.dumps(out))

    Note: shp_obj is shapely object of type: Polygon, MultiPolygon, LineString and Point
    """
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
