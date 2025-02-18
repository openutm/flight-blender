from math import atan2, cos, radians, sin, sqrt
import shapely
from pyproj import Geod
from shapely.geometry import box as shapley_box


def build_view_port_box(view_port_coords) -> shapely.geometry.box:
    box = shapley_box(
        view_port_coords[0],
        view_port_coords[1],
        view_port_coords[2],
        view_port_coords[3],
    )
    return box


def get_view_port_area(view_box: shapley_box) -> int:
    geod = Geod(ellps="WGS84")
    area = abs(geod.geometry_area_perimeter(view_box)[0])
    return area


def get_view_port_diagonal_length_kms(view_port_coords) -> float:
    # Source: https://stackoverflow.com/questions/19412462/getting-distance-between-two-points-based-on-latitude-longitude
    R = 6373.0

    lat1 = radians(min(view_port_coords[0], view_port_coords[2]))
    lon1 = radians(min(view_port_coords[1], view_port_coords[3]))
    lat2 = radians(max(view_port_coords[0], view_port_coords[2]))
    lon2 = radians(max(view_port_coords[1], view_port_coords[3]))

    dlon = lon2 - lon1
    dlat = lat2 - lat1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    diagonal_distance = R * c
    return diagonal_distance


def check_view_port(view_port_coords) -> bool:
    """
    Checks if the given viewport coordinates are valid.
    The function expects a list of four coordinates representing the viewport:
    [lat1, lng1, lat2, lng2]. It verifies that the list contains exactly four
    coordinates and that these coordinates fall within the valid ranges:
    - Latitude (lat1, lat2) must be between -90 and 90 degrees.
    - Longitude (lng1, lng2) must be between -180 and 360 degrees.
    Args:
        view_port_coords (list): A list of four float values representing the
                                 viewport coordinates [lat1, lng1, lat2, lng2].
    Returns:
        bool: True if the viewport coordinates are valid, False otherwise.
    """

    if len(view_port_coords) != 4:
        return False

    lat_min, lat_max = sorted(view_port_coords[::2])
    lng_min, lng_max = sorted(view_port_coords[1::2])

    if not (-90 <= lat_min < 90 and -90 < lat_max <= 90 and -180 <= lng_min < 360 and -180 < lng_max <= 360):
        return False

    return True