"""Pure business logic helpers for SCD (Strategic Coordination) operations.

Extracted from dss_scd_client.py to separate validation and conversion logic
from HTTP/DB concerns. These classes have zero I/O dependencies.
"""

from dataclasses import asdict

import arrow
import shapely.geometry
from loguru import logger
from pyproj import Proj
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from flight_blender.config import settings
from flight_blender.domain_types.common import VALID_OPERATIONAL_INTENT_STATES
from flight_blender.domain_types.scd import (
    FlightPlanningInjectionData,
    LatLng,
    OperationalIntentDetailsUSSResponse,
    OperationalIntentState,
    OperationalIntentTestInjection,
    UasState,
    UsageState,
    Volume4D,
)

# Valid uas_state values for an incoming flight-planning test injection.
_VALID_PLANNING_UAS_STATES = frozenset(
    {UasState.Nominal.value, UasState.OffNominal.value, UasState.Contingent.value, UasState.NotSpecified.value}
)
# usage_state values for which off-nominal volumes are not permitted.
_NO_OFF_NOMINAL_USAGE_STATES = frozenset({UsageState.Planned.value, UsageState.InUse.value})
# Operational-intent states accepted by the test injection (Contingent intentionally excluded).
_VALID_OPERATIONAL_INTENT_INJECTION_STATES = frozenset(
    {OperationalIntentState.Accepted.value, OperationalIntentState.Activated.value, OperationalIntentState.Nonconforming.value}
)
# Operational-intent states for which off-nominal volumes are not permitted.
_NO_OFF_NOMINAL_OPERATIONAL_INTENT_STATES = frozenset({OperationalIntentState.Accepted.value, OperationalIntentState.Activated.value})


class FlightPlanningDataValidator:
    def __init__(self, incoming_flight_planning_data: FlightPlanningInjectionData):
        self.flight_planning_data = incoming_flight_planning_data

    def validate_flight_planning_state(self) -> bool:
        if self.flight_planning_data.uas_state not in _VALID_PLANNING_UAS_STATES:
            logger.error("Invalid uas_state: %s" % self.flight_planning_data.uas_state)
            return False
        return True

    def validate_flight_planning_off_nominals(self) -> bool:
        return not (self.flight_planning_data.usage_state in _NO_OFF_NOMINAL_USAGE_STATES and bool(self.flight_planning_data.off_nominal_volumes))

    def validate_flight_planning_test_data(self) -> bool:
        return all([self.validate_flight_planning_state(), self.validate_flight_planning_off_nominals()])


class OperationalIntentValidator:
    def __init__(self, operational_intent_data: OperationalIntentTestInjection):
        self.operational_intent_data = operational_intent_data

    def validate_operational_intent_state(self) -> bool:
        if self.operational_intent_data.state not in _VALID_OPERATIONAL_INTENT_INJECTION_STATES:
            logger.error("Invalid operational intent state: %s" % self.operational_intent_data.state)
            return False
        return True

    def validate_operational_intent_state_off_nominals(self) -> bool:
        return not (
            self.operational_intent_data.state in _NO_OFF_NOMINAL_OPERATIONAL_INTENT_STATES
            and bool(self.operational_intent_data.off_nominal_volumes)
        )

    def validate_operational_intent_test_data(self) -> bool:
        return all([self.validate_operational_intent_state(), self.validate_operational_intent_state_off_nominals()])


class PeerOperationalIntentValidator:
    """This class validates operational intent data received from a peer USS"""

    def validate_individual_operational_intent(self, operational_intent: OperationalIntentDetailsUSSResponse) -> bool:
        all_checks_passed: list[bool] = []
        if operational_intent.reference.state not in VALID_OPERATIONAL_INTENT_STATES:
            logger.debug(f"Error in received operational intent state, it is not valid {operational_intent.reference.state}")
            all_checks_passed.append(False)
        else:
            all_checks_passed.append(True)

        if not isinstance(operational_intent.details.priority, int):
            logger.debug(f"Error in received operational intent priority, it is not one an integer {operational_intent.details.priority}")
            all_checks_passed.append(False)
        else:
            all_checks_passed.append(True)

        return all(all_checks_passed)

    def validate_nearby_operational_intents(self, nearby_operational_intents: list[OperationalIntentDetailsUSSResponse]) -> bool:
        all_nearby_operational_intents_valid: list[bool] = []

        for nearby_operational_intent in nearby_operational_intents:
            operational_intent_valid = self.validate_individual_operational_intent(operational_intent=nearby_operational_intent)
            all_nearby_operational_intents_valid.append(operational_intent_valid)
        return all(all_nearby_operational_intents_valid)


class VolumesValidator:
    def validate_volume_start_end_date(self, volume: Volume4D) -> bool:
        now = arrow.now()
        thirty_days_from_now = now.shift(days=30)
        volume_start_datetime = arrow.get(volume.time_start.value)

        if volume_start_datetime > thirty_days_from_now:
            return False
        else:
            return True

    def validate_volume_times_within_limits_for_creation(self, volume: Volume4D) -> bool:
        """This method validates that the operational intent is not in the past"""
        now = arrow.now()
        volume_start_datetime = arrow.get(volume.time_start.value)
        start_time_valid = True
        delta = now - volume_start_datetime
        time_delta_seconds = delta.total_seconds()
        if time_delta_seconds > 5:
            start_time_valid = False

        return start_time_valid

    def validate_polygon_vertices(self, volume: Volume4D) -> bool:
        v = asdict(volume)
        cur_volume = v["volume"]
        if "outline_polygon" in cur_volume.keys():
            outline_polygon = cur_volume["outline_polygon"]
            if outline_polygon:
                total_vertices = len(outline_polygon["vertices"])
                if total_vertices < 3:
                    return False
        return True

    def pre_operational_intent_creation_checks(self, volumes: list[Volume4D]) -> bool:
        all_volume_start_time_ok = []
        for volume in volumes:
            start_time_validated = self.validate_volume_times_within_limits_for_creation(volume)
            all_volume_start_time_ok.append(start_time_validated)

        return all(all_volume_start_time_ok)

    def validate_volumes(self, volumes: list[Volume4D]) -> bool:
        all_volumes_ok = []
        for volume in volumes:
            volume_validated = self.validate_polygon_vertices(volume)
            volume_start_end_time_validated = self.validate_volume_start_end_date(volume)
            all_volumes_ok.append(volume_validated)
            all_volumes_ok.append(volume_start_end_time_validated)
        return all(all_volumes_ok)


class VolumesConverter:
    """A class to convert a Volume4D in to GeoJSON"""

    def __init__(self):
        self.geo_json = {"type": "FeatureCollection", "features": []}
        self.utm_zone = settings.UTM_ZONE
        self.all_volume_features = []
        self.upper_altitude = 0
        self.lower_altitude = 0
        self.altitude_ref = "W84"
        self.time_start = arrow.now().isoformat()
        self.time_end = arrow.now().isoformat()

    def utm_converter(self, shapely_shape: shapely.geometry, inverse: bool = False) -> shapely.geometry.shape:
        """A helper function to convert from lat / lon to UTM coordinates for buffering. tracks. This is the UTM projection (https://en.wikipedia.org/wiki/Universal_Transverse_Mercator_coordinate_system), we use Zone 54N which encompasses Japan, this zone has to be set for each locale / city. Adapted from https://gis.stackexchange.com/questions/325926/buffering-geometry-with-points-in-wgs84-using-shapely"""

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

    def convert_volumes_to_geojson(self, volumes: list[Volume4D]) -> None:
        all_upper_altitudes = []
        all_lower_altitudes = []
        volume_time_starts = []
        volume_time_ends = []
        for volume in volumes:
            all_upper_altitudes.append(volume.volume.altitude_upper.value)
            all_lower_altitudes.append(volume.volume.altitude_lower.value)

            volume_time_starts.append(arrow.get(volume.time_start.value))
            volume_time_ends.append(arrow.get(volume.time_end.value))

            geo_json_features = self._convert_volume_to_geojson_feature(volume)
            self.geo_json["features"] += geo_json_features

        if not volumes:
            return

        self.time_start = min(volume_time_starts).isoformat()
        self.time_end = max(volume_time_ends).isoformat()
        self.upper_altitude = max(all_upper_altitudes)
        self.lower_altitude = min(all_lower_altitudes)

    def get_volume_bounds(self) -> list[LatLng]:
        union = unary_union(self.all_volume_features)
        rect_bounds = union.minimum_rotated_rectangle
        g_c = []
        for coord in list(rect_bounds.exterior.coords):
            ll = LatLng(lat=float(coord[1]), lng=float(coord[0]))
            g_c.append(asdict(ll))
        return g_c

    def get_minimum_rotated_rectangle(self) -> BaseGeometry:
        union = unary_union(self.all_volume_features)
        return union

    def get_earliest_time_from_volumes(self) -> str:
        return self.time_start

    def get_latest_time_from_volumes(self) -> str:
        return self.time_end

    def get_bounds(self) -> list[float]:
        union = unary_union(self.all_volume_features)
        rect_bounds = union.bounds
        return rect_bounds

    def _convert_volume_to_geojson_feature(self, volume: Volume4D):
        v = asdict(volume)
        cur_volume = v["volume"]
        geo_json_features = []
        if "outline_polygon" in cur_volume.keys():
            outline_polygon = cur_volume["outline_polygon"]
            if outline_polygon:
                point_list = []
                for vertex in outline_polygon["vertices"]:
                    p = Point(vertex["lng"], vertex["lat"])
                    point_list.append(p)
                outline_polygon = Polygon([[p.x, p.y] for p in point_list])
                self.all_volume_features.append(outline_polygon)
                outline_p = shapely.geometry.mapping(outline_polygon)

                polygon_feature = {
                    "type": "Feature",
                    "properties": {"max_altitude": volume.volume.altitude_upper.value, "min_altitude": volume.volume.altitude_lower.value},
                    "geometry": outline_p,
                }
                geo_json_features.append(polygon_feature)

        if "outline_circle" in cur_volume.keys():
            outline_circle = cur_volume["outline_circle"]
            if outline_circle:
                circle_radius = outline_circle["radius"]["value"]
                center_point = Point(outline_circle["center"]["lng"], outline_circle["center"]["lat"])
                utm_center = self.utm_converter(shapely_shape=center_point)
                buffered_cicle = utm_center.buffer(circle_radius)
                converted_circle = self.utm_converter(buffered_cicle, inverse=True)
                self.all_volume_features.append(converted_circle)
                outline_c = shapely.geometry.mapping(converted_circle)

                circle_feature = {
                    "type": "Feature",
                    "properties": {"max_altitude": volume.volume.altitude_upper.value, "min_altitude": volume.volume.altitude_lower.value},
                    "geometry": outline_c,
                }

                geo_json_features.append(circle_feature)

        return geo_json_features
