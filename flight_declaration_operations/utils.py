import json
import logging
from dataclasses import asdict
from importlib import import_module
from os import environ as env

import arrow
import shapely.geometry
from dotenv import find_dotenv, load_dotenv
from geojson import FeatureCollection
from pyproj import Proj
from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union

from common.data_definitions import (
    DEFAULT_UAV_CLIMB_RATE_M_PER_S,
    DEFAULT_UAV_DESCENT_RATE_M_PER_S,
    DEFAULT_UAV_SPEED_M_PER_S,
)
from flight_blender.settings import CUSTOM_VOLUME_4D_GENERATION_CLASS
from scd_operations.scd_data_definitions import (
    Altitude,
    LatLngPoint,
    OperationalIntentBoundsTimeAltitude,
    OperationalIntentUSSDetails,
    PartialCreateOperationalIntentReference,
    Time,
    Volume3D,
    Volume4D,
)
from scd_operations.scd_data_definitions import Polygon as Plgn

logger = logging.getLogger("django")

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


class OperationalIntentsConverter:
    """
    A class to convert operational intents into GeoJSON format and perform various operations related to operational intents.
    Attributes:
        geo_json (dict): A dictionary representing the GeoJSON structure.
        utm_zone (str): The UTM zone used for coordinate conversion.
        all_features (list): A list to store all features for union operations.
    Methods:
        __init__():
            Initializes the OperationalIntentsConverter with default values.
        utm_converter(shapely_shape: shapely.geometry, inverse: bool = False) -> shapely.geometry.shape:
            Converts coordinates between latitude/longitude and UTM.
        convert_operational_intent_to_geo_json(volumes: List[Volume4D]):
            Converts a list of Volume4D objects to GeoJSON format.
        create_partial_operational_intent_ref(start_datetime: str, end_datetime: str, geo_json_fc: FeatureCollection, priority: int, state: str = "Accepted") -> PartialCreateOperationalIntentReference:
            Creates a partial operational intent reference from given parameters.
        convert_geo_json_to_volume_4_d(geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> List[Volume4D]:
            Converts a GeoJSON FeatureCollection to a list of Volume4D objects.
        buffer_point_to_volume4d(lat: float, lng: float, max_altitude: float, min_altitude: float, start_datetime: str, end_datetime: str) -> Volume4D:
            Generates a new Volume4D object based on a buffered point.
        get_geo_json_bounds() -> str:
            Returns the bounding box of all features in GeoJSON format.
        _convert_operational_intent_to_geojson_feature(volume: Volume4D):
            Converts a Volume4D object to GeoJSON features.
    """

    def __init__(self):
        """
        Initializes the instance with default values.
        Attributes:
            geo_json (dict): A dictionary representing an empty GeoJSON FeatureCollection.
            utm_zone (str): The UTM zone, defaulting to "54N" if not provided in the environment variables.
            all_features (list): An empty list to store all features.
        """

        self.geo_json = {"type": "FeatureCollection", "features": []}
        self.utm_zone = env.get("UTM_ZONE", "54N")  # Default Zone for Switzerland

        self.all_features = []

    def generate_bounds_altitude_time_for_volumes(
        self,
        operational_intent_details_payload: OperationalIntentUSSDetails,
        flight_declaration_id: str,
    ) -> OperationalIntentBoundsTimeAltitude:
        all_volumes = operational_intent_details_payload.volumes
        min_altitude = float("inf")
        max_altitude = float("-inf")
        start_time = None
        end_time = None

        for volume in all_volumes:
            # convert volume to shapely shape
            if volume.volume.altitude_lower.value < min_altitude:
                min_altitude = volume.volume.altitude_lower.value
            if volume.volume.altitude_upper.value > max_altitude:
                max_altitude = volume.volume.altitude_upper.value
            start_time = min(
                start_time or arrow.get(volume.time_start.value),
                arrow.get(volume.time_start.value),
            )
            end_time = max(
                end_time or arrow.get(volume.time_end.value),
                arrow.get(volume.time_end.value),
            )

        self.convert_operational_intent_to_geo_json(all_volumes)
        bounds = self.get_geo_json_bounds()

        operational_intent_bounds_time_altitude = OperationalIntentBoundsTimeAltitude(
            bounds=bounds,
            alt_min=min_altitude,
            alt_max=max_altitude,
            start_datetime=start_time.isoformat(),
            end_datetime=end_time.isoformat(),
            flight_declaration_id=flight_declaration_id,
        )
        return operational_intent_bounds_time_altitude

    def utm_converter(
        self,
        shapely_shape: shapely.geometry.base.BaseGeometry,
        inverse: bool = False,
    ) -> shapely.geometry.base.BaseGeometry:
        """
        Converts coordinates between latitude/longitude and UTM.

        Args:
            shapely_shape (shapely.geometry.base.BaseGeometry): The shapely geometry object to convert.
            inverse (bool): If True, converts from UTM to lat/lon. If False, converts from lat/lon to UTM.

        Returns:
            shapely.geometry.base.BaseGeometry: The converted shapely geometry object.


        A helper function to convert from lat / lon to UTM coordinates for buffering. tracks. This is the UTM projection (https://en.wikipedia.org/wiki/Universal_Transverse_Mercator_coordinate_system), we use Zone 54N which encompasses Japan, this zone has to be set for each locale / city. Adapted from https://gis.stackexchange.com/questions/325926/buffering-geometry-with-points-in-wgs84-using-shapely"
        """
        proj = Proj(proj="utm", zone=self.utm_zone, ellps="WGS84", datum="WGS84")

        geo_interface = shapely_shape.__geo_interface__
        point_or_polygon = geo_interface["type"]
        coordinates = geo_interface["coordinates"]

        if point_or_polygon == "Polygon":
            new_coordinates = [[proj(*point, inverse=inverse) for point in linring] for linring in coordinates]
        elif point_or_polygon == "Point":
            new_coordinates = proj(*coordinates, inverse=inverse)
        else:
            raise RuntimeError(f"Unexpected geo_interface type: {point_or_polygon}")

        return shapely.geometry.shape({"type": point_or_polygon, "coordinates": tuple(new_coordinates)})

    def convert_operational_intent_to_geo_json(self, volumes: list[Volume4D]):
        """
        Converts a list of Volume4D objects to GeoJSON format and appends the resulting features
        to the geo_json attribute.

        Args:
            volumes (List[Volume4D]): A list of Volume4D objects representing the operational intent.

        Returns:
            None
        """

        for volume in volumes:
            geo_json_features = self._convert_operational_intent_to_geojson_feature(volume)

            self.geo_json["features"] += geo_json_features

    def create_partial_operational_intent_ref(
        self,
        start_datetime: str,
        end_datetime: str,
        geo_json_fc: FeatureCollection,
        priority: int,
        state: str = "Accepted",
    ) -> PartialCreateOperationalIntentReference:
        """
        Creates a partial operational intent reference from given parameters.

        Args:
            start_datetime (str): The start time in RFC3339 format.
            end_datetime (str): The end time in RFC3339 format.
            geo_json_fc (FeatureCollection): The GeoJSON FeatureCollection representing the operational intent.
            priority (int): The priority of the operational intent.
            state (str): The state of the operational intent. Defaults to "Accepted".

        Returns:
            PartialCreateOperationalIntentReference: The created partial operational intent reference.
        """
        all_v4d = self.convert_geo_json_to_volume_4_d(
            geo_json_fc=geo_json_fc,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )

        op_int_ref = PartialCreateOperationalIntentReference(volumes=all_v4d, state=state, priority=priority, off_nominal_volumes=[])

        return op_int_ref

    def convert_geo_json_to_volume_4_d(self, geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> list[Volume4D]:
        """
        Converts a GeoJSON FeatureCollection to a list of Volume4D objects.

        Args:
            geo_json_fc (FeatureCollection): The GeoJSON FeatureCollection to convert.
            start_datetime (str): The start time in RFC3339 format.
            end_datetime (str): The end time in RFC3339 format.

        Returns:
            List[Volume4D]: A list of Volume4D objects.
        """

        if CUSTOM_VOLUME_4D_GENERATION_CLASS:
            module_name, class_name = CUSTOM_VOLUME_4D_GENERATION_CLASS.rsplit(".", 1)
            module = import_module(module_name)
            CustomVolumeGenerator = getattr(module, class_name)
            custom_volume_generator = CustomVolumeGenerator(
                default_uav_speed_m_per_s=DEFAULT_UAV_SPEED_M_PER_S,
                default_uav_climb_rate_m_per_s=DEFAULT_UAV_CLIMB_RATE_M_PER_S,
                default_uav_descent_rate_m_per_s=DEFAULT_UAV_DESCENT_RATE_M_PER_S,
            )

            for feature in geo_json_fc["features"]:
                geom = feature["geometry"]
                shapely_geom = shape(geom)
                buffered_geom = shapely_geom.buffer(0.0005)
                self.all_features.append(buffered_geom)
            all_volumes = custom_volume_generator.build_v4d_from_geojson(
                geo_json_fc=geo_json_fc,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )

            self.convert_operational_intent_to_geo_json(all_volumes)
            return all_volumes
        else:
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

    def buffer_point_to_volume4d(
        self,
        lat: float,
        lng: float,
        max_altitude: float,
        min_altitude: float,
        start_datetime: str,
        end_datetime: str,
    ) -> Volume4D:
        """
        Generates a new Volume4D object based on a buffered point.

        Args:
            lat (float): Latitude of the point.
            lng (float): Longitude of the point.
            max_altitude (float): Maximum altitude in meters.
            min_altitude (float): Minimum altitude in meters.
            start_datetime (str): Start time in RFC3339 format.
            end_datetime (str): End time in RFC3339 format.

        Returns:
            Volume4D: The generated Volume4D object.
        """
        point = Point(lng, lat)
        buffered_shape = point.buffer(0.0001)

        coordinates = list(zip(*buffered_shape.exterior.coords.xy))
        polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]

        volume_3d = Volume3D(
            outline_polygon=Plgn(vertices=polygon_vertices),
            altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
            altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
        )

        volume_4d = Volume4D(
            volume=volume_3d,
            time_start=Time(format="RFC3339", value=start_datetime),
            time_end=Time(format="RFC3339", value=end_datetime),
        )

        return volume_4d

    def get_geo_json_bounds(self) -> str:
        combined_features = unary_union(self.all_features)
        bnd_tuple = combined_features.bounds
        bounds = ",".join([f"{x:.7f}" for x in bnd_tuple])

        return bounds

    def _convert_operational_intent_to_geojson_feature(self, volume: Volume4D):
        """
        Converts a Volume4D object to GeoJSON features.

        Args:
            volume (Volume4D): The Volume4D object representing the operational intent.

        Returns:
            list: A list of GeoJSON features.
        """
        geo_json_features = []
        volume_dict = asdict(volume.volume)
        time_start = volume.time_start.value
        time_end = volume.time_end.value

        if "outline_polygon" in volume_dict and volume_dict["outline_polygon"] is not None:
            outline_polygon = volume_dict["outline_polygon"]
            point_list = [Point(vertex["lng"], vertex["lat"]) for vertex in outline_polygon["vertices"]]
            outline_polygon = Polygon([[p.x, p.y] for p in point_list])
            self.all_features.append(outline_polygon)

            oriented_polygon = shapely.geometry.polygon.orient(outline_polygon)
            outline_polygon_geojson = shapely.geometry.mapping(oriented_polygon)

            polygon_feature = {
                "type": "Feature",
                "properties": {"time_start": time_start, "time_end": time_end},
                "geometry": outline_polygon_geojson,
            }
            geo_json_features.append(polygon_feature)

        if "outline_circle" in volume_dict and volume_dict["outline_circle"] is not None:
            outline_circle = volume_dict["outline_circle"]
            circle_radius = outline_circle["radius"]["value"]
            center_point = Point(outline_circle["center"]["lng"], outline_circle["center"]["lat"])
            utm_center = self.utm_converter(shapely_shape=center_point)
            buffered_circle = utm_center.buffer(circle_radius)
            converted_circle = self.utm_converter(buffered_circle, inverse=True)
            self.all_features.append(converted_circle)

            outline_circle_geojson = shapely.geometry.mapping(converted_circle)

            circle_feature = {
                "type": "Feature",
                "properties": {"time_start": time_start, "time_end": time_end},
                "geometry": outline_circle_geojson,
            }
            geo_json_features.append(circle_feature)

        return geo_json_features
