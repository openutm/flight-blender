## This file checks the conformance of a operation per the AMC stated in the EU Conformance monitoring service
import json
import logging
from os import environ as env
from typing import Optional

import arrow
from dotenv import find_dotenv, load_dotenv
from shapely.geometry import Point
from shapely.geometry import Polygon as Plgn

from common.database_operations import FlightBlenderDatabaseReader
from conformance_monitoring_operations.data_definitions import PolygonAltitude
from scd_operations.scd_data_definitions import LatLngPoint

from .conformance_state_helper import ConformanceChecksList
from .data_helper import cast_to_volume4d

logger = logging.getLogger("django")


ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


def is_time_between(begin_time, end_time, check_time=None):
    # If check time is not given, default to current UTC time
    # Source: https://stackoverflow.com/questions/10048249/how-do-i-determine-if-current-time-is-within-a-specified-range-using-pythons-da
    check_time = check_time or arrow.now()
    if begin_time < end_time:
        return check_time >= begin_time and check_time <= end_time
    else:  # crosses midnight
        return check_time >= begin_time or check_time <= end_time


class FlightBlenderConformanceEngine:
    def is_operation_conformant_via_telemetry(
        self,
        flight_declaration_id: str,
        aircraft_id: str,
        telemetry_location: LatLngPoint,
        altitude_m_wgs_84: float,
    ) -> int:
        """This method performs the conformance sequence per AMC1 Article 13(1) as specified in the EU AMC / GM on U-Space regulation.
        This method is called every time a telemetry has been sent into Flight Blender. Specifically, it checks this once a telemetry has been sent:
         - C2 Check if flight authorization is granted
         - C3 Match telemetry from aircraft with the flight authorization
         - C4 Determine whether the aircraft is subject to an accepted and activated flight authorization
         - C5 Check if flight operation is activated
         - C6 Check if telemetry is within start / end time of the operation
         - C7 (A)(B) Check if the aircraft complies with deviation thresholds / 4D volume
         - C8 Check if it is near a GeoFence and / breaches one

        """
        my_database_reader = FlightBlenderDatabaseReader()
        now = arrow.now()
        USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))

        flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)

        if USSP_NETWORK_ENABLED:
            flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_id(
                flight_declaration_id=flight_declaration_id
            )
        else:
            flight_operational_intent_reference = True

        operational_intent_details = my_database_reader.get_operational_intent_details_by_flight_declaration(flight_declaration=flight_declaration)

        # C2 Check
        if not flight_operational_intent_reference or not flight_declaration:
            logger.error(
                f"Error in getting flight authorization and declaration for {flight_declaration_id}, cannot continue with conformance checks, C2 Check failed."
            )
            logger.error("Conformance check failed, flight authorization not found, raising code {ConformanceChecksList.C2}")
            return ConformanceChecksList.C2

        operation_start_time = arrow.get(flight_declaration.start_datetime)
        operation_end_time = arrow.get(flight_declaration.end_datetime)

        # C3 Check
        if flight_declaration.aircraft_id != aircraft_id:
            logger.error(
                f"Aircraft ID mismatch for {flight_declaration_id}, C3 Check failed: Flight Declaration {flight_declaration.aircraft_id} != Telemetry {aircraft_id}"
            )
            logger.error(f"Raising error code {ConformanceChecksList.C3}")
            return ConformanceChecksList.C3

        # C4, C5 Check
        if flight_declaration.state in [0, 5, 6, 7, 8]:
            logger.error(f"Flight state is invalid for {flight_declaration_id}, C4 Check failed.")
            logger.error(f"Raising error code {ConformanceChecksList.C4}")
            return ConformanceChecksList.C4

        if flight_declaration.state not in [2, 3, 4]:
            logger.error(f"Flight state is not activated for {flight_declaration_id}, C5 Check failed.")
            logger.error(f"Raising Error code {ConformanceChecksList.C5}")
            return ConformanceChecksList.C5

        # C6 Check
        if not is_time_between(
            begin_time=operation_start_time,
            end_time=operation_end_time,
            check_time=now,
        ):
            logger.error(f"Telemetry is not within operation time for {flight_declaration_id}, C6 Check failed.")
            logger.error(f"Raising Error code {ConformanceChecksList.C6}")
            return ConformanceChecksList.C6

        # C7 Check: Check if the aircraft is within the 4D volume
        # all_volumes = []
        # if USSP_NETWORK_ENABLED:
        all_volumes = json.loads(operational_intent_details.volumes)

        lng = float(telemetry_location.lng)
        lat = float(telemetry_location.lat)
        rid_location = Point(lng, lat)
        all_polygon_altitudes: list[PolygonAltitude] = []

        for v in all_volumes:
            v4d = cast_to_volume4d(v)
            altitude_lower = v4d.volume.altitude_lower.value
            altitude_upper = v4d.volume.altitude_upper.value
            outline_polygon = v4d.volume.outline_polygon
            point_list = [Point(vertex.lng, vertex.lat) for vertex in outline_polygon.vertices]
            outline_polygon = Plgn([[p.x, p.y] for p in point_list])

            pa = PolygonAltitude(
                polygon=outline_polygon,
                altitude_upper=altitude_upper,
                altitude_lower=altitude_lower,
            )
            all_polygon_altitudes.append(pa)

        rid_obs_within_all_volumes = []
        rid_obs_within_altitudes = []

        for p in all_polygon_altitudes:
            is_within = rid_location.within(p.polygon)
            altitude_conformant = p.altitude_lower <= altitude_m_wgs_84 <= p.altitude_upper
            rid_obs_within_all_volumes.append(is_within)
            rid_obs_within_altitudes.append(altitude_conformant)

        aircraft_bounds_conformant = any(rid_obs_within_all_volumes)
        aircraft_altitude_conformant = any(rid_obs_within_altitudes)

        if not aircraft_altitude_conformant:
            logger.error(f"Aircraft altitude is not conformant for {flight_declaration_id}, C7b Check failed.")
            logger.error(f"Raising Error code {ConformanceChecksList.C7b}")
            return ConformanceChecksList.C7b

        if not aircraft_bounds_conformant:
            logger.error(f"Aircraft bounds are not conformant for {flight_declaration_id}, C7a Check failed.")
            logger.error(f"Raising Error code {ConformanceChecksList.C7a}")
            return ConformanceChecksList.C7a

        # C8 Check: Check if aircraft is not breaching any active Geofences
        geofences = my_database_reader.get_active_geofences()
        for geofence in geofences:
            geofence_geojson = json.loads(geofence.raw_geo_fence)
            features = geofence_geojson.get("features", [])
            for feature in features:
                geometry = feature.get("geometry", {})
                geofence_type = geometry.get("type")
                coordinates = geometry.get("coordinates", [])

                if geofence_type == "Polygon":
                    if self._is_within_geofence(rid_location, coordinates[0]):
                        logger.error(f"Aircraft is breaching an active GeoFence for {flight_declaration_id}, C8 Check failed.")
                        logger.error(f"Raising Error code {ConformanceChecksList.C8}")
                        return ConformanceChecksList.C8

                elif geofence_type == "MultiPolygon":
                    for polygon_coords in coordinates:
                        if self._is_within_geofence(rid_location, polygon_coords[0]):
                            logger.error(f"Aircraft is breaching an active GeoFence for {flight_declaration_id}, C8 Check failed.")
                            logger.error(f"Raising Error code {ConformanceChecksList.C8}")
                            return ConformanceChecksList.C8

        return 100

    def _is_within_geofence(self, rid_location: Point, coordinates: list) -> bool:
        """Helper method to check if a location is within a geofence."""
        geofence_polygon = Plgn(coordinates)
        return rid_location.within(geofence_polygon)

    def check_flight_operational_intent_reference_conformance(self, flight_declaration_id: str) -> bool:
        """This method checks the conformance of a flight authorization independent of telemetry observations being sent:
        C9 a/b Check if telemetry is being sent
        C10 Check operation state that it not ended and the time limit of the flight authorization has passed
        C11 Check if a Flight authorization object exists
        """
        # Flight Operation and Flight Authorization exists, create a notifications helper

        my_database_reader = FlightBlenderDatabaseReader()
        now = arrow.now()
        flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)
        flight_operational_intent_reference_exists = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_id(
            flight_declaration_id=flight_declaration_id
        )
        # C11 Check
        if not flight_operational_intent_reference_exists:
            # if flight state is accepted, then change it to ended and delete from dss
            logger.info(f"Flight authorization does not exist for {flight_declaration_id}, C11 Check failed.")
            logger.info(f"Raising Error code {ConformanceChecksList.C11}")
            return False
        # The time the most recent telemetry was sent
        latest_telemetry_datetime = flight_declaration.latest_telemetry_datetime
        # Check the current time is within the start / end date time +/- 15 seconds TODO: trim this window as it is to broad
        fifteen_seconds_before_now = now.shift(seconds=-15)
        fifteen_seconds_after_now = now.shift(seconds=15)
        # C10 state check
        # allowed_states = ['Activated', 'Nonconforming', 'Contingent']
        allowed_states = [2, 3, 4]
        if flight_declaration.state not in allowed_states:
            # set state as ended
            logger.info(f"Flight operation state is ended for {flight_declaration_id}, C10 Check failed.")
            logger.info(f"Raising Error code {ConformanceChecksList.C10}")
            return False

        # C9 state check
        # Operation is supposed to start check if telemetry is bieng submitted (within the last minute)
        if latest_telemetry_datetime:
            if not fifteen_seconds_before_now <= latest_telemetry_datetime <= fifteen_seconds_after_now:
                logger.info(f"No telemetry data being sent for {flight_declaration_id}, C9 Check failed.")
                logger.info(f"Raising Error code {ConformanceChecksList.C9b}")
                return False
        else:
            # declare state as contingent
            logger.info(f"Flight operation state is contingent for {flight_declaration_id}, C9 Check failed.")
            logger.info(f"Raising Error code {ConformanceChecksList.C9a}")
            return False

        return True
