"""
Shared geometry utilities.
"""


def point_in_polygon(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    """Return whether the point ``(lon, lat)`` lies inside *ring*.

    Pure-Python ray-casting (even-odd rule).  *ring* is a list of
    ``(lon, lat)`` tuples; the ring is treated as closed (the last vertex is
    implicitly joined back to the first).
    """
    n = len(ring)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def bounds_contains_point(bounds: str, lon: float, lat: float) -> bool:
    """Return True if the comma-separated ``minx,miny,maxx,maxy`` bounds cover the point."""
    try:
        minx, miny, maxx, maxy = (float(x) for x in bounds.split(","))
    except (ValueError, AttributeError):
        return False
    return minx <= lon <= maxx and miny <= lat <= maxy


def compute_bounds(coordinates: list[list[float]]) -> str:
    """Return ``"minx,miny,maxx,maxy"`` (7 dp) for a set of ``[lon, lat]`` pairs."""
    if not coordinates:
        return ""
    lons = [c[0] for c in coordinates]
    lats = [c[1] for c in coordinates]
    bnd = (min(lons), min(lats), max(lons), max(lats))
    return ",".join(f"{x:.7f}" for x in bnd)
