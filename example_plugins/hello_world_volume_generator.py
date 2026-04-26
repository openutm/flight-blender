"""Example: speed-aware volume 4D generator plugin.

A volume 4D generator that divides the overall time window across
GeoJSON features proportionally to segment length, then adds a
configurable safety buffer around each geometry.  This produces
time-sequenced volumes that approximate actual UAV transit rather
than assigning the full time window to every feature.

To activate, set the environment variable:

.. code-block:: bash

    FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR=example_plugins.hello_world_volume_generator.HelloWorldVolumeGenerator

See PLUGINS.md for the full guide.
"""

import arrow
from geojson import FeatureCollection
from loguru import logger
from pyproj import Geod
from shapely.geometry import shape

from scd_operations.scd_data_definitions import (
    Altitude,
    LatLngPoint,
    Time,
    Volume3D,
    Volume4D,
)
from scd_operations.scd_data_definitions import Polygon as Plgn

# Safety margin added around each geometry (degrees, ~55 m at equator).
_BUFFER_DEG = 0.0005


class HelloWorldVolumeGenerator:
    """Speed-aware volume 4D generator.

    Splits the overall time window across features in proportion to
    their geodesic length, giving each Volume4D a realistic time
    slice instead of the full window.
    """

    def __init__(
        self,
        default_uav_speed_m_per_s: float,
        default_uav_climb_rate_m_per_s: float,
        default_uav_descent_rate_m_per_s: float,
    ):
        self.default_uav_speed_m_per_s = default_uav_speed_m_per_s
        self.default_uav_climb_rate_m_per_s = default_uav_climb_rate_m_per_s
        self.default_uav_descent_rate_m_per_s = default_uav_descent_rate_m_per_s
        self.geod = Geod(ellps="WGS84")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _feature_length_m(self, feature: dict) -> float:
        """Return the geodesic length (metres) of a feature's geometry."""
        shapely_geom = shape(feature["geometry"])
        return abs(self.geod.geometry_length(shapely_geom))

    def _polygon_from_buffered(self, shapely_geom) -> Plgn:
        buffered = shapely_geom.buffer(_BUFFER_DEG)
        coords = list(zip(*buffered.exterior.coords.xy))
        vertices = [LatLngPoint(lat=c[1], lng=c[0]) for c in coords[:-1]]
        return Plgn(vertices=vertices)

    # ------------------------------------------------------------------
    # Public API (called by the framework)
    # ------------------------------------------------------------------

    def build_v4d_from_geojson(
        self,
        geo_json_fc: FeatureCollection,
        start_datetime: str,
        end_datetime: str,
    ) -> list[Volume4D]:
        features = geo_json_fc["features"]
        logger.info("Generating time-sequenced Volume4Ds for %d features", len(features))

        start = arrow.get(start_datetime)
        end = arrow.get(end_datetime)
        total_secs = (end - start).total_seconds()

        # Compute per-feature length so we can proportion time.
        lengths = [self._feature_length_m(f) for f in features]
        total_length = sum(lengths) or 1.0  # avoid division by zero

        all_v4d: list[Volume4D] = []
        cursor = start

        for idx, (feature, length_m) in enumerate(zip(features, lengths)):
            fraction = length_m / total_length
            duration_secs = max(total_secs * fraction, 1.0)
            segment_end = cursor.shift(seconds=duration_secs)
            # Clamp the last segment so it never overshoots end_datetime.
            if idx == len(features) - 1 or segment_end > end:
                segment_end = end

            max_altitude = feature["properties"]["max_altitude"]["meters"]
            min_altitude = feature["properties"]["min_altitude"]["meters"]

            shapely_geom = shape(feature["geometry"])
            outline = self._polygon_from_buffered(shapely_geom)

            volume_3d = Volume3D(
                outline_polygon=outline,
                altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
                altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
            )
            all_v4d.append(
                Volume4D(
                    volume=volume_3d,
                    time_start=Time(format="RFC3339", value=cursor.isoformat()),
                    time_end=Time(format="RFC3339", value=segment_end.isoformat()),
                )
            )
            cursor = segment_end

        logger.info("Generated %d Volume4Ds spanning %s → %s", len(all_v4d), start, cursor)
        return all_v4d
