from typing import Tuple

from pyproj import CRS, Transformer

# Cache the transformer at module level — CRS/Transformer creation is expensive
# and the transform() method is thread-safe.
_WGS84_3D: CRS = CRS.from_epsg(4979)
_EGM96_MSL: CRS = CRS.from_epsg(5773)
_TRANSFORMER: Transformer = Transformer.from_crs(_WGS84_3D, _EGM96_MSL, always_xy=True)


def wgs84_to_barometric(lat: float, lon: float, hae_meters: float) -> Tuple[float, float]:
    """
    Converts WGS 84 Height Above Ellipsoid (HAE) to MSL and Pressure Altitude.

    Returns:
        Tuple[float, float]: (MSL Height in meters, Pressure Altitude in meters)
    """
    # Perform the vertical datum shift
    # Result returns a Tuple of (lon, lat, orthometric_height)
    result: Tuple[float, float, float] = _TRANSFORMER.transform(lon, lat, hae_meters)
    msl_height: float = result[2]

    # Under Standard Atmosphere (ISA) conditions,
    # Geometric MSL height is equivalent to Pressure Altitude.
    pressure_altitude: float = msl_height

    return msl_height, pressure_altitude
