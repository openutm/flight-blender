import arrow
import geojson
from geojson import Feature, FeatureCollection
from loguru import logger
from pyproj import Geod
from shapely.geometry import Point, shape

from common.data_definitions import (
    DEFAULT_UAV_CLIMB_RATE_M_PER_S,
    DEFAULT_UAV_DESCENT_RATE_M_PER_S,
    DEFAULT_UAV_SPEED_M_PER_S,
)
from scd_operations.scd_data_definitions import (
    Altitude,
    LatLngPoint,
    Time,
    Volume3D,
    Volume4D,
)
from scd_operations.scd_data_definitions import Polygon as Plgn


class CustomVolumeGenerator:
    def __init__(
        self,
        default_uav_speed_m_per_s: float,
        default_uav_climb_rate_m_per_s: float,
        default_uav_descent_rate_m_per_s: float,
    ):
        self.default_uav_speed_m_per_s = default_uav_speed_m_per_s
        self.default_uav_climb_rate_m_per_s = default_uav_climb_rate_m_per_s
        self.default_uav_descent_rate_m_per_s = default_uav_descent_rate_m_per_s
        self.all_features = []

    def _break_linestring_to_smaller_pieces(self, line_feature: Feature, piece_length_m: float = 5.5) -> list[Feature]:
        """
        Break a GeoJSON LineString into smaller pieces based on a specified maximum length,
        assuming the drone flies at 5.5 m/s, so each piece can be traversed in approximately 1 second.

        This function iterates through the coordinates of the input LineString, accumulating points
        into pieces until the cumulative distance reaches the specified piece_length_m. If a segment
        would exceed the limit, it interpolates a new point at the exact distance needed using geodesic
        calculations.

            line_feature (Feature): The GeoJSON Feature representing the LineString to be broken down.
            piece_length_m (float): The maximum length of each piece in meters. Defaults to 5.5 meters.

        Returns:
            list[Feature]: A list of GeoJSON Features, each representing a smaller piece of the original
            LineString with the same properties.

        Notes:
            - Uses the WGS84 ellipsoid for geodesic calculations via pyproj's Geod.
            - az12: The forward azimuth (bearing) from the start point to the end point in degrees.
            - az21: The back azimuth (bearing) from the end point to the start point in degrees.
            - If the LineString has fewer than 2 coordinates, it returns the original feature unchanged.
        Args:
            line_feature (Feature): The GeoJSON Feature representing the LineString.
            piece_length_m (float): The maximum length of each piece in meters. Default is 5.5 meters.
        Returns
            List[Feature]: A list of GeoJSON Features representing the smaller pieces of the original LineString.
        """
        geod = Geod(ellps="WGS84")
        line_coords = line_feature["geometry"]["coordinates"]
        if len(line_coords) < 2:
            return [line_feature]

        pieces = []
        current_piece = [line_coords[0]]
        current_length = 0.0
        i = 1

        while i < len(line_coords):
            start_point = current_piece[-1]
            end_point = line_coords[i]
            az12, az21, dist = geod.inv(start_point[0], start_point[1], end_point[0], end_point[1])

            if current_length + dist <= piece_length_m:
                current_piece.append(end_point)
                current_length += dist
                i += 1
            else:
                remaining = piece_length_m - current_length
                lon2, lat2, az = geod.fwd(start_point[0], start_point[1], az12, remaining)
                interp_point = [lon2, lat2]
                current_piece.append(interp_point)
                pieces.append(current_piece)
                current_piece = [interp_point]
                current_length = 0.0
                # i not incremented, so next iteration handles remaining segment

        if current_piece:
            pieces.append(current_piece)

        new_features = []
        for piece in pieces:
            new_feature = Feature(
                geometry={"type": "LineString", "coordinates": piece},
                properties=line_feature["properties"],
            )
            new_features.append(new_feature)
        logger.info(f"Broken into {len(new_features)} pieces.")
        return new_features

    def build_v4d_from_geojson(self, geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> list[Volume4D]:
        # Iterate through each feature in the collection, check if all the feature type is linestring
        feature_types = set(feature["geometry"]["type"] for feature in geo_json_fc["features"])
        if len(feature_types) == 1 and "LineString" in feature_types:
            collection_type = "all_linesstrings"
        elif len(feature_types) == 1 and "Polygon" in feature_types:
            collection_type = "all_polygons"
        else:
            collection_type = "linestrings_and_polygons"

        if collection_type == "all_linesstrings":
            return self.build_v4d_from_linestrings(
                geo_json_fc=geo_json_fc,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
        else:
            return self.build_v4d_from_mixed_polygons_and_linestrings(
                geo_json_fc=geo_json_fc,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )

    def build_v4d_from_mixed_polygons_and_linestrings(self, geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> list[Volume4D]:
        all_v4d = []
        for feature in geo_json_fc["features"]:
            geom = feature["geometry"]
            max_altitude = feature["properties"]["max_altitude"]["meters"]
            min_altitude = feature["properties"]["min_altitude"]["meters"]
            shapely_geom = shape(geom)
            buffered_geom = shapely_geom.buffer(0.0005)
            self.all_features.append(buffered_geom)

            coordinates = list(zip(*buffered_geom.exterior.coords.xy))
            polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]

            volume_3d = Volume3D(
                outline_polygon=Plgn(vertices=polygon_vertices),
                altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
                altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
            )

            time_start = feature["properties"].get("start_time", start_datetime)
            time_end = feature["properties"].get("end_time", end_datetime)

            volume_4d = Volume4D(
                volume=volume_3d,
                time_start=Time(format="RFC3339", value=time_start),
                time_end=Time(format="RFC3339", value=time_end),
            )

            all_v4d.append(volume_4d)

        return all_v4d

    def build_v4d_from_linestrings(self, geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> list[Volume4D]:
        """
        Builds a list of Volume4D objects from a GeoJSON FeatureCollection containing linestring features.
        This method processes each linestring feature in the provided FeatureCollection, breaks it into smaller
        pieces based on a default UAV speed, calculates adjusted flight times including climb and descent phases,
        and generates Volume4D objects for each piece. Each Volume4D includes a buffered 3D volume (polygon with
        altitude bounds) and time intervals derived from the start and end datetimes.
        Args:
            geo_json_fc (FeatureCollection): A GeoJSON FeatureCollection containing features with linestring
                geometries and properties including 'max_altitude' and 'min_altitude' (in meters).
            start_datetime (str): The start datetime string for the flight (used to calculate takeoff time).
            end_datetime (str): The end datetime string for the flight (used to calculate landing time).
        Returns:
            list[Volume4D]: A list of Volume4D objects, each representing a spatial-temporal volume for a piece
                of the linestring with buffered geometry, altitude bounds, and calculated time intervals.
        Note:
            - Assumes default UAV speed, climb rate, and descent rate constants are defined elsewhere.
            - Time calculations use the 'arrow' library for datetime manipulation.
            - Buffering is applied to the geometry for safety margins.
        """
        geo_json_features = geo_json_fc["features"]
        # sort the features based on the id property
        geo_json_features.sort(key=lambda x: x["properties"].get("id", 0))
        geo_json_fc["features"] = geo_json_features
        all_v4d = []
        _takeoff_start = arrow.get(start_datetime).shift(seconds=1).isoformat()
        _landing_time = arrow.get(end_datetime).shift(seconds=-1).isoformat()

        # Get the first and last coordinates for takeoff and landing
        first_feature = geo_json_fc["features"][0]
        last_feature = geo_json_fc["features"][-1]
        first_coord = first_feature["geometry"]["coordinates"][0]
        last_coord = last_feature["geometry"]["coordinates"][-1]
        takeoff_location = LatLngPoint(lat=first_coord[1], lng=first_coord[0])
        landing_location = LatLngPoint(lat=last_coord[1], lng=last_coord[0])

        max_altitude = first_feature["properties"]["max_altitude"]["meters"]
        min_altitude = first_feature["properties"]["min_altitude"]["meters"]

        # Create takeoff volume
        takeoff_volume_4d = self._create_buffered_volume_4d(
            point=takeoff_location,
            max_altitude=max_altitude,
            min_altitude=min_altitude,
            time_start=start_datetime,
            time_end=_takeoff_start,
        )
        all_v4d.append(takeoff_volume_4d)

        # Create landing volume
        landing_volume_4d = self._create_buffered_volume_4d(
            point=landing_location,
            max_altitude=max_altitude,
            min_altitude=min_altitude,
            time_start=_landing_time,
            time_end=end_datetime,
        )
        all_v4d.append(landing_volume_4d)

        # Process each feature
        for feature in geo_json_fc["features"]:
            max_altitude = feature["properties"]["max_altitude"]["meters"]
            min_altitude = feature["properties"]["min_altitude"]["meters"]

            broken_down_features = self._break_linestring_to_smaller_pieces(line_feature=feature, piece_length_m=self.default_uav_speed_m_per_s * 3)

            num_pieces = len(broken_down_features)
            total_flight_time_s = num_pieces * 3  # Assuming each piece takes 3 seconds
            climb_time_s = abs(max_altitude - min_altitude) / self.default_uav_climb_rate_m_per_s
            descent_time_s = abs(max_altitude - min_altitude) / self.default_uav_descent_rate_m_per_s
            adjusted_flight_time_s = total_flight_time_s + climb_time_s + descent_time_s

            for idx, piece in enumerate(broken_down_features):
                piece_start_time = arrow.get(_takeoff_start).shift(seconds=int(idx * 3 + climb_time_s)).isoformat()
                piece_end_time = arrow.get(piece_start_time).shift(seconds=3).isoformat()  # Each piece takes 3 seconds

                piece_geom = piece["geometry"]
                shapely_piece_geom = shape(piece_geom)
                buffered_shape = shapely_piece_geom.buffer(0.0001)

                coordinates = list(zip(*buffered_shape.exterior.coords.xy))
                polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]

                volume_3d = Volume3D(
                    outline_polygon=Plgn(vertices=polygon_vertices),
                    altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
                    altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
                )

                volume_4d = Volume4D(
                    volume=volume_3d,
                    time_start=Time(format="RFC3339", value=piece_start_time),
                    time_end=Time(format="RFC3339", value=piece_end_time),
                )
                all_v4d.append(volume_4d)

        # Check if the last piece's end time matches the landing time
        if all_v4d and all_v4d[-1].time_end.value != _landing_time:
            logger.warning(f"Piece end time {all_v4d[-1].time_end.value} does not match landing time {_landing_time}")
            logger.info("The landing time has been changed and is computed using the default UAV speed.")

        return all_v4d

    def _create_buffered_volume_4d(self, point: LatLngPoint, max_altitude: float, min_altitude: float, time_start: str, time_end: str) -> Volume4D:
        """Helper method to create a buffered Volume4D from a point."""
        shapely_point = Point(point.lng, point.lat)
        buffered_geom = shapely_point.buffer(0.0005)
        coordinates = list(zip(*buffered_geom.exterior.coords.xy))
        polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]

        volume_3d = Volume3D(
            outline_polygon=Plgn(vertices=polygon_vertices),
            altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
            altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
        )

        return Volume4D(
            volume=volume_3d,
            time_start=Time(format="RFC3339", value=time_start),
            time_end=Time(format="RFC3339", value=time_end),
        )
