from typing import Tuple

from pyproj import CRS, Transformer


def wgs84_to_barometric(lat: float, lon: float, hae_meters: float) -> Tuple[float, float]:
    """
    Converts WGS 84 Height Above Ellipsoid (HAE) to MSL and Pressure Altitude.

    Returns:
        Tuple[float, float]: (MSL Height in meters, Pressure Altitude in meters)
    """
    # Define CRSs with explicit typing
    # EPSG 4979: WGS 84 (3D)
    # EPSG 5773: EGM96 geoid height
    wgs84_3d: CRS = CRS.from_epsg(4979)
    egm96_msl: CRS = CRS.from_epsg(5773)

    # Initialize the Transformer
    transformer: Transformer = Transformer.from_crs(wgs84_3d, egm96_msl, always_xy=True)

    # Perform the vertical datum shift
    # Result returns a Tuple of (lon, lat, orthometric_height)
    result: Tuple[float, float, float] = transformer.transform(lon, lat, hae_meters)
    msl_height: float = result[2]

    # Under Standard Atmosphere (ISA) conditions,
    # Geometric MSL height is equivalent to Pressure Altitude.
    pressure_altitude: float = msl_height

    return msl_height, pressure_altitude
