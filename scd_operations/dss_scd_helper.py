import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from os import environ as env
from typing import List, Optional, Union

import arrow
import requests
import shapely.geometry
import tldextract
import urllib3
from dotenv import find_dotenv, load_dotenv
from pyproj import Proj
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from auth_helper import dss_auth_helper
from auth_helper.common import get_redis
from common.auth_token_audience_helper import generate_audience_from_base_url
from common.data_definitions import FLIGHT_OPINT_KEY, VALID_OPERATIONAL_INTENT_STATES
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from rid_operations import rtree_helper

from .flight_planning_data_definitions import FlightPlanningInjectionData
from .scd_data_definitions import (
    Altitude,
    Circle,
    CommonDSS2xxResponse,
    CommonDSS4xxResponse,
    CommonPeer9xxResponse,
    DeleteOperationalIntentConstuctor,
    DeleteOperationalIntentResponse,
    DeleteOperationalIntentResponseSuccess,
    FlightPlanCurrentStatus,
    ImplicitSubscriptionParameters,
    LatLng,
    LatLngPoint,
    NotifyPeerUSSPostPayload,
    OperationalIntentDetailsUSSResponse,
    OperationalIntentReference,
    OperationalIntentReferenceDSSResponse,
    OperationalIntentStorage,
    OperationalIntentSubmissionError,
    OperationalIntentSubmissionStatus,
    OperationalIntentSubmissionSuccess,
    OperationalIntentTestInjection,
    OperationalIntentUpdateErrorResponse,
    OperationalIntentUpdateRequest,
    OperationalIntentUpdateResponse,
    OperationalIntentUpdateSuccessResponse,
    OperationalIntentUSSDetails,
    OpInttoCheckDetails,
    OpIntUpdateCheckResultCodes,
    QueryOperationalIntentPayload,
    Radius,
    ShouldSendtoDSSProcessingResponse,
    SubscriberToNotify,
    SubscriptionState,
    Time,
    USSNotificationResponse,
    Volume3D,
    Volume4D,
)
from .scd_data_definitions import Polygon as Plgn

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)

logger = logging.getLogger("django")


def is_time_within_time_period(start_time: datetime, end_time: datetime, time_to_check: datetime):
    return time_to_check >= start_time or time_to_check <= end_time


class FlightPlanningDataValidator:
    def __init__(self, incoming_flight_planning_data: FlightPlanningInjectionData):
        self.flight_planning_data = incoming_flight_planning_data

    def validate_flight_planning_state(self) -> bool:
        try:
            assert self.flight_planning_data.uas_state in [
                "Nominal",
                "OffNominal",
                "Contingent",
                "NotSpecified",
            ]
        except AssertionError as ae:
            logger.error(ae)
            return False
        else:
            return True

    def validate_flight_planning_off_nominals(self) -> bool:
        if self.flight_planning_data.usage_state in ["Planned", "InUse"] and bool(self.flight_planning_data.off_nominal_volumes):
            return False
        else:
            return True

    def validate_flight_planning_test_data(self) -> bool:
        flight_planning_test_data_ok = []
        flight_planning_state_ok = self.validate_flight_planning_state()
        flight_planning_off_nominals_ok = self.validate_flight_planning_off_nominals()
        flight_planning_test_data_ok.append(flight_planning_state_ok)
        flight_planning_test_data_ok.append(flight_planning_off_nominals_ok)

        return all(flight_planning_test_data_ok)


class OperationalIntentValidator:
    def __init__(self, operational_intent_data: OperationalIntentTestInjection):
        self.operational_intent_data = operational_intent_data

    def validate_operational_intent_state(self) -> bool:
        try:
            assert self.operational_intent_data.state in [
                "Accepted",
                "Activated",
                "Nonconforming",
            ]
        except AssertionError as ae:
            logger.error(ae)
            return False
        else:
            return True

    def validate_operational_intent_state_off_nominals(self) -> bool:
        if self.operational_intent_data.state in ["Accepted", "Activated"] and bool(self.operational_intent_data.off_nominal_volumes):
            return False
        else:
            return True

    def validate_operational_intent_test_data(self) -> bool:
        operational_intent_test_data_ok = []
        operational_intent_state_ok = self.validate_operational_intent_state()
        state_off_nominals_ok = self.validate_operational_intent_state_off_nominals()
        operational_intent_test_data_ok.append(operational_intent_state_ok)
        operational_intent_test_data_ok.append(state_off_nominals_ok)
        return all(operational_intent_test_data_ok)


class PeerOperationalIntentValidator:
    """This class validates operational intent data received from a peer USS"""

    def validate_individual_operational_intent(self, operational_intent: OperationalIntentDetailsUSSResponse) -> bool:
        all_checks_passed: List[bool] = []
        try:
            assert operational_intent.reference.state in VALID_OPERATIONAL_INTENT_STATES
        except AssertionError:
            logger.debug("Error in received operational intent state, the state declared is invalid: %s" % operational_intent.reference.state)
            all_checks_passed.append(False)
        else:
            all_checks_passed.append(True)

        try:
            assert isinstance(operational_intent.details.priority, int)
        except AssertionError:
            logger.debug("Error in received operational intent priority, it is not one an integer %s" % operational_intent.details.priority)
            all_checks_passed.append(False)
        else:
            all_checks_passed.append(True)

        return all(all_checks_passed)

    def validate_nearby_operational_intents(self, nearby_operational_intents: List[OperationalIntentDetailsUSSResponse]) -> bool:
        all_nearby_operational_intents_valid: List[bool] = []

        for nearby_operational_intent in nearby_operational_intents:
            operational_intent_valid = self.validate_individual_operational_intent(operational_intent=nearby_operational_intent)
            all_nearby_operational_intents_valid.append(operational_intent_valid)
        return all(all_nearby_operational_intents_valid)


class VolumesValidator:
    def validate_volume_start_end_date(self, volume: Volume4D) -> bool:
        now = arrow.now()
        thirty_days_from_now = now.shift(days=30)
        volume_start_datetime = arrow.get(volume.time_start.value)
        # volume_end_datetime = arrow.get(volume.time_end)

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
                # Check the vertices is at least 3
                if total_vertices < 3:
                    return False
        return True

    def pre_operational_intent_creation_checks(self, volumes: List[Volume4D]) -> bool:
        all_volume_start_time_ok = []
        for volume in volumes:
            start_time_validated = self.validate_volume_times_within_limits_for_creation(volume)
            all_volume_start_time_ok.append(start_time_validated)

        return all(all_volume_start_time_ok)

    def validate_volumes(self, volumes: List[Volume4D]) -> bool:
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
        self.utm_zone = env.get("UTM_ZONE", "54N")
        self.all_volume_features = []

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
            raise RuntimeError("Unexpected geo_interface type: {}".format(point_or_polygon))

        return shapely.geometry.shape({"type": point_or_polygon, "coordinates": tuple(new_coordinates)})

    def convert_volumes_to_geojson(self, volumes: List[Volume4D]) -> None:
        for volume in volumes:
            geo_json_features = self._convert_volume_to_geojson_feature(volume)
            self.geo_json["features"] += geo_json_features

    def get_volume_bounds(self) -> List[LatLng]:
        union = unary_union(self.all_volume_features)
        rect_bounds = union.minimum_rotated_rectangle
        g_c = []
        for coord in list(rect_bounds.exterior.coords):
            ll = LatLng(lat=float(coord[1]), lng=float(coord[0]))
            g_c.append(asdict(ll))
        return g_c

    def get_minimum_rotated_rectangle(self) -> Polygon:
        union = unary_union(self.all_volume_features)
        return union

    def get_bounds(self) -> List[float]:
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
                    "properties": {},
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
                    "properties": {},
                    "geometry": outline_c,
                }

                geo_json_features.append(circle_feature)

        return geo_json_features


class OperationalIntentReferenceHelper:
    """
    A class to parse Operational Intent References into Dataclass objects
    """

    def parse_stored_operational_intent_details(self, operation_id: str) -> OperationalIntentStorage:
        r = get_redis()
        flight_opint = FLIGHT_OPINT_KEY + str(operation_id)

        op_int_details_raw = r.get(flight_opint)
        existing_op_int_details_raw = json.loads(op_int_details_raw)

        all_subscribers = existing_op_int_details_raw["success_response"]["subscribers"]
        subscribers = []
        for s in all_subscribers:
            all_s = s["subscriptions"]
            for cur_s in all_s:
                sub = SubscriptionState(
                    subscription_id=cur_s["subscription_id"],
                    notification_index=cur_s["notification_index"],
                )
                subscribers.append(sub)
            s_n = SubscriberToNotify(subscriptions=all_s, uss_base_url=s["uss_base_url"])
            subscribers.append(s_n)

        operational_intent_respose_raw = existing_op_int_details_raw["success_response"]["operational_intent_reference"]
        operational_intent_details_raw = existing_op_int_details_raw["operational_intent_details"]
        volumes = operational_intent_details_raw["volumes"]
        off_nominal_volumes = operational_intent_details_raw["off_nominal_volumes"]
        priority = operational_intent_details_raw["priority"]
        state = operational_intent_details_raw["state"]

        operational_intent_reference_dss_repsonse = OperationalIntentReferenceDSSResponse(
            id=operational_intent_respose_raw["id"],
            manager=operational_intent_respose_raw["manager"],
            uss_availability=operational_intent_respose_raw["uss_availability"],
            version=operational_intent_respose_raw["version"],
            state=operational_intent_respose_raw["state"],
            ovn=operational_intent_respose_raw["ovn"],
            time_start=Time(
                format=operational_intent_respose_raw["time_start"]["format"],
                value=operational_intent_respose_raw["time_start"]["value"],
            ),
            time_end=Time(
                format=operational_intent_respose_raw["time_start"]["format"],
                value=operational_intent_respose_raw["time_start"]["value"],
            ),
            uss_base_url=operational_intent_respose_raw["uss_base_url"],
            subscription_id=operational_intent_respose_raw["subscription_id"],
        )

        all_volumes: List[Volume4D] = []
        all_off_nominal_volumes: List[Volume4D] = []
        for volume in volumes:
            volume4D = self.parse_volume_to_volume4D(volume=volume)
            all_volumes.append(volume4D)

        for off_nominal_volume in off_nominal_volumes:
            off_nominal_volume4D = self.parse_volume_to_volume4D(volume=off_nominal_volume)
            all_off_nominal_volumes.append(off_nominal_volume4D)

        operational_intent_details = OperationalIntentTestInjection(
            volumes=all_volumes,
            priority=priority,
            off_nominal_volumes=all_off_nominal_volumes,
            state=state,
        )

        stored = OperationalIntentStorage(
            bounds=existing_op_int_details_raw["bounds"],
            start_time=existing_op_int_details_raw["start_time"],
            end_time=existing_op_int_details_raw["end_time"],
            alt_max=existing_op_int_details_raw["alt_max"],
            alt_min=existing_op_int_details_raw["alt_min"],
            success_response=OperationalIntentSubmissionSuccess(
                subscribers=subscribers,
                operational_intent_reference=operational_intent_reference_dss_repsonse,
            ),
            operational_intent_details=operational_intent_details,
        )
        return stored

    def parse_and_load_stored_flight_opint(self, operation_id: str) -> Optional[OperationalIntentDetailsUSSResponse]:
        """
        Given a stored flight operational intent, get the details of the operational intent
        """
        r = get_redis()
        flight_opint = FLIGHT_OPINT_KEY + str(operation_id)
        if r.exists(flight_opint):
            op_int_details_raw = r.get(flight_opint)
            op_int_details = json.loads(op_int_details_raw)
            reference_full = op_int_details["success_response"]["operational_intent_reference"]
            # dss_response_subscribers = op_int_details["success_response"]["subscribers"]
            # flight_blender_base_url = env.get("FLIGHTBLENDER_FQDN", "http://localhost:8000")

            # for subscriber in dss_response_subscribers:
            #     subscriptions = subscriber["subscriptions"]
            #     uss_base_url = subscriber["uss_base_url"]
            #     if flight_blender_base_url == uss_base_url:
            #         for s in subscriptions:
            #             subscription_id = s["subscription_id"]
            #             break
            details_full = op_int_details["operational_intent_details"]
            # Load existing opint details

            stored_operational_intent_id = reference_full["id"]
            stored_manager = reference_full["manager"]
            stored_uss_availability = reference_full["uss_availability"]
            stored_version = reference_full["version"]
            stored_state = reference_full["state"]
            stored_ovn = reference_full["ovn"]
            stored_uss_base_url = reference_full["uss_base_url"]
            stored_subscription_id = reference_full["subscription_id"]

            stored_time_start = Time(
                format=reference_full["time_start"]["format"],
                value=reference_full["time_start"]["value"],
            )
            stored_time_end = Time(
                format=reference_full["time_end"]["format"],
                value=reference_full["time_end"]["value"],
            )

            stored_priority = details_full["priority"]
            stored_off_nominal_volumes = details_full["off_nominal_volumes"]

            details = self.parse_operational_intent_details(
                operational_intent_details=details_full,
                priority=stored_priority,
                off_nominal_volumes=stored_off_nominal_volumes,
            )

            reference = OperationalIntentReferenceDSSResponse(
                id=stored_operational_intent_id,
                manager=stored_manager,
                uss_availability=stored_uss_availability,
                version=stored_version,
                state=stored_state,
                ovn=stored_ovn,
                time_start=stored_time_start,
                time_end=stored_time_end,
                uss_base_url=stored_uss_base_url,
                subscription_id=stored_subscription_id,
            )
            return OperationalIntentDetailsUSSResponse(details=details, reference=reference)

        return None

    def parse_volume_to_volume4D(self, volume) -> Volume4D:
        outline_polygon = None
        outline_circle = None
        if "outline_polygon" in volume["volume"].keys():
            all_vertices = volume["volume"]["outline_polygon"]["vertices"]
            polygon_verticies = []
            for vertex in all_vertices:
                v = LatLngPoint(lat=vertex["lat"], lng=vertex["lng"])
                polygon_verticies.append(v)
            outline_polygon = Plgn(polygon_verticies)

        if "outline_circle" in volume["volume"].keys() and volume["volume"]["outline_circle"]:
            circle_center = LatLngPoint(
                lat=volume["volume"]["outline_circle"]["center"]["lat"],
                lng=volume["volume"]["outline_circle"]["center"]["lng"],
            )
            circle_radius = Radius(
                value=volume["volume"]["outline_circle"]["radius"]["value"],
                units=volume["volume"]["outline_circle"]["radius"]["units"],
            )
            outline_circle = Circle(center=circle_center, radius=circle_radius)

        altitude_lower = Altitude(
            value=volume["volume"]["altitude_lower"]["value"],
            reference=volume["volume"]["altitude_lower"]["reference"],
            units=volume["volume"]["altitude_lower"]["units"],
        )
        altitude_upper = Altitude(
            value=volume["volume"]["altitude_upper"]["value"],
            reference=volume["volume"]["altitude_upper"]["reference"],
            units=volume["volume"]["altitude_upper"]["units"],
        )
        volume3D = Volume3D(
            outline_circle=outline_circle,
            outline_polygon=outline_polygon,
            altitude_lower=altitude_lower,
            altitude_upper=altitude_upper,
        )

        time_start = Time(
            format=volume["time_start"]["format"],
            value=volume["time_start"]["value"],
        )
        time_end = Time(format=volume["time_end"]["format"], value=volume["time_end"]["value"])

        volume4D = Volume4D(volume=volume3D, time_start=time_start, time_end=time_end)
        return volume4D

    def parse_operational_intent_details(self, operational_intent_details, priority: int, off_nominal_volumes=None) -> OperationalIntentUSSDetails:
        volumes = operational_intent_details["volumes"]
        all_volumes: List[Volume4D] = []
        all_off_nominal_volumes: List[Volume4D] = []
        for volume in volumes:
            volume4D = self.parse_volume_to_volume4D(volume=volume)
            all_volumes.append(volume4D)

        for off_nominal_volume in off_nominal_volumes:
            off_nominal_volume4D = self.parse_volume_to_volume4D(volume=off_nominal_volume)
            all_off_nominal_volumes.append(off_nominal_volume4D)

        o_i_d = OperationalIntentUSSDetails(
            volumes=all_volumes,
            priority=priority,
            off_nominal_volumes=all_off_nominal_volumes,
        )
        return o_i_d

    def update_ovn_in_stored_opint_ref(self):
        pass

    def parse_operational_intent_reference_from_dss(self, operational_intent_reference) -> OperationalIntentReferenceDSSResponse:
        time_start = Time(
            format=operational_intent_reference["time_start"]["format"],
            value=operational_intent_reference["time_start"]["value"],
        )

        time_end = Time(
            format=operational_intent_reference["time_end"]["format"],
            value=operational_intent_reference["time_end"]["value"],
        )

        op_int_reference = OperationalIntentReferenceDSSResponse(
            id=operational_intent_reference["id"],
            uss_availability=operational_intent_reference["uss_availability"],
            manager=operational_intent_reference["manager"],
            version=operational_intent_reference["version"],
            state=operational_intent_reference["state"],
            ovn=operational_intent_reference["ovn"],
            time_start=time_start,
            time_end=time_end,
            uss_base_url=operational_intent_reference["uss_base_url"],
            subscription_id=operational_intent_reference["subscription_id"],
        )

        return op_int_reference


class SCDOperations:
    def __init__(self):
        self.dss_base_url = env.get("DSS_BASE_URL", "0")
        self.r = get_redis()
        self.database_reader = FlightBlenderDatabaseReader()
        self.database_writer = FlightBlenderDatabaseWriter()

    def get_nearby_operational_intents(self, volumes: List[Volume4D]) -> List[OperationalIntentDetailsUSSResponse]:
        # This method checks the USS network for any other volume in the airspace and queries the individual USS for data

        all_uss_op_int_details = []
        auth_token = self.get_auth_token()
        # Query the DSS for operational intentns
        query_op_int_url = self.dss_base_url + "dss/v1/operational_intent_references/query"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        flight_blender_base_url = env.get("FLIGHTBLENDER_FQDN", "http://flight-blender:8000")
        my_op_int_ref_helper = OperationalIntentReferenceHelper()
        all_uss_operational_intent_details = []

        for volume in volumes:
            op_int_details_retrieved = False
            operational_intent_references = []
            area_of_interest = QueryOperationalIntentPayload(area_of_interest=volume)
            logger.info("Querying DSS for operational intents in the area..")
            logger.debug("Area of interest {area_of_interest}".format(area_of_interest=area_of_interest))
            try:
                operational_intent_ref_response = requests.post(
                    query_op_int_url,
                    json=json.loads(json.dumps(asdict(area_of_interest))),
                    headers=headers,
                )
            except Exception as re:
                logger.error("Error in getting operational intent for the volume %s " % re)
            else:
                # The DSS returned operational intent references as a list
                dss_operational_intent_references = operational_intent_ref_response.json()
                logger.debug(
                    "DSS Response {dss_operational_intent_references}".format(dss_operational_intent_references=dss_operational_intent_references)
                )
                operational_intent_references = dss_operational_intent_references["operational_intent_references"]

            # Query the operational intent reference details
            for operational_intent_reference_detail in operational_intent_references:
                # Get the USS URL endpoint
                dss_op_int_details_url = self.dss_base_url + "dss/v1/operational_intent_references/" + operational_intent_reference_detail["id"]
                # get new auth token for USS
                try:
                    op_int_uss_details = requests.get(dss_op_int_details_url, headers=headers)
                except Exception as e:
                    logger.error("Error in getting operational intent details %s" % e)
                else:
                    operational_intent_reference = op_int_uss_details.json()
                    o_i_r = operational_intent_reference["operational_intent_reference"]
                    o_i_r_formatted = OperationalIntentReferenceDSSResponse(
                        id=o_i_r["id"],
                        manager=o_i_r["manager"],
                        uss_availability=o_i_r["uss_availability"],
                        version=o_i_r["version"],
                        state=o_i_r["state"],
                        ovn=o_i_r["ovn"],
                        time_start=o_i_r["time_start"],
                        time_end=o_i_r["time_end"],
                        uss_base_url=o_i_r["uss_base_url"],
                        subscription_id=o_i_r["subscription_id"],
                    )
                    # if o_i_r_formatted.uss_base_url != flight_blender_base_url:
                    all_uss_operational_intent_details.append(o_i_r_formatted)

            for current_uss_operational_intent_detail in all_uss_operational_intent_details:
                # check the USS for flight volume by using the URL to see if this is stored in Flight Blender, DSS will return all intent details including our own
                current_uss_base_url = current_uss_operational_intent_detail.uss_base_url
                if current_uss_base_url == flight_blender_base_url:
                    # The opint is from Flight Blender itself
                    # No need to query peer USS, just update the ovn and process the volume locally

                    flight_authorization = self.database_reader.get_flight_authorization_by_operational_intent_ref_id(
                        str(current_uss_operational_intent_detail.id)
                    )
                    # TODO: Check if this can be removed
                    if flight_authorization:
                        self.database_writer.update_flight_authorization_ovn(
                            flight_authorization=flight_authorization, ovn=current_uss_operational_intent_detail.ovn
                        )

                    operational_intent_id = str(flight_authorization.declaration.id)
                    flight_opint = FLIGHT_OPINT_KEY + operational_intent_id

                    if self.r.exists(flight_opint):
                        op_int_details_raw = self.r.get(flight_opint)
                        op_int_details = json.loads(op_int_details_raw)
                        op_int_ref = op_int_details["success_response"]["operational_intent_reference"]
                        op_int_det = op_int_details["operational_intent_details"]
                        # Update the ovn
                        op_int_ref["ovn"] = current_uss_operational_intent_detail.ovn

                    op_int_details_retrieved = True

                else:  # This operational intent details is from a peer uss, need to query peer USS
                    uss_audience = generate_audience_from_base_url(base_url=current_uss_base_url)
                    uss_auth_token = self.get_auth_token(audience=uss_audience)
                    logger.debug("Auth Token {uss_auth_token}".format(uss_auth_token=uss_auth_token))
                    uss_headers = {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + uss_auth_token["access_token"],
                    }
                    uss_operational_intent_url = current_uss_base_url + "/uss/v1/operational_intents/" + current_uss_operational_intent_detail.id

                    logger.debug("Querying USS: {current_uss_base_url}".format(current_uss_base_url=current_uss_base_url))
                    try:
                        uss_operational_intent_request = requests.get(uss_operational_intent_url, headers=uss_headers)
                    except urllib3.exceptions.NameResolutionError:
                        logger.info("URLLIB error")
                        raise ConnectionError("Could not reach peer USS.. ")

                    except (
                        requests.exceptions.ConnectTimeout,
                        requests.exceptions.HTTPError,
                        requests.exceptions.ReadTimeout,
                        requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError,
                    ) as e:
                        logger.error("Connection error details..")
                        logger.error(e)
                        logger.error(
                            "Error in getting operational intent id {uss_op_int_id} details from uss with base url {uss_base_url}".format(
                                uss_op_int_id=current_uss_operational_intent_detail.id,
                                uss_base_url=current_uss_base_url,
                            )
                        )
                        op_int_details_retrieved = False
                        logger.info("Raising connection Error 1")
                        raise ConnectionError("Could not reach peer USS..")

                    else:
                        # Verify status of the response from the USS
                        if uss_operational_intent_request.status_code == 200:
                            # Request was successful
                            operational_intent_details_json = uss_operational_intent_request.json()
                            op_int_details_retrieved = True
                            # outline_polygon = None
                            # outline_circle = None

                            op_int_det = operational_intent_details_json["operational_intent"]["details"]
                            op_int_ref = operational_intent_details_json["operational_intent"]["reference"]
                        # The attempt to get data from the USS in the network failed
                        elif uss_operational_intent_request.status_code in [
                            401,
                            400,
                            404,
                            500,
                        ]:
                            logger.debug(uss_operational_intent_request.json())
                            logger.error(
                                "Error in querying peer USS about operational intent (ID: {uss_op_int_id}) details from uss with base url {uss_base_url}".format(
                                    uss_op_int_id=current_uss_operational_intent_detail.id,
                                    uss_base_url=current_uss_base_url,
                                )
                            )

                if op_int_details_retrieved:
                    op_int_reference: OperationalIntentReferenceDSSResponse = my_op_int_ref_helper.parse_operational_intent_reference_from_dss(
                        operational_intent_reference=op_int_ref
                    )
                    my_opint_ref_helper = OperationalIntentReferenceHelper()
                    all_volumes = op_int_det["volumes"]
                    all_v4d = []
                    for cur_volume in all_volumes:
                        cur_v4d = my_opint_ref_helper.parse_volume_to_volume4D(volume=cur_volume)
                        all_v4d.append(cur_v4d)

                    all_off_nominal_volumes = op_int_det["off_nominal_volumes"]
                    all_off_nominal_v4d = []
                    for cur_off_nominal_volume in all_off_nominal_volumes:
                        cur_off_nominal_v4d = my_opint_ref_helper.parse_volume_to_volume4D(volume=cur_off_nominal_volume)
                        all_off_nominal_v4d.append(cur_off_nominal_v4d)

                    op_int_detail = OperationalIntentUSSDetails(
                        volumes=all_v4d,
                        priority=op_int_det["priority"],
                        off_nominal_volumes=all_off_nominal_v4d,
                    )

                    uss_op_int_details = OperationalIntentDetailsUSSResponse(reference=op_int_reference, details=op_int_detail)
                    all_uss_op_int_details.append(uss_op_int_details)

        return all_uss_op_int_details

    def get_auth_token(self, audience: str = None):
        my_authorization_helper = dss_auth_helper.AuthorityCredentialsGetter()
        if audience is None:
            audience = env.get("DSS_SELF_AUDIENCE", 0)
        try:
            assert audience
        except AssertionError:
            logger.error("Error in getting Authority Access Token DSS_SELF_AUDIENCE is not set in the environment")
        auth_token = {}
        try:
            auth_token = my_authorization_helper.get_cached_credentials(audience=audience, token_type="scd")
        except Exception as e:
            logger.error("Error in getting Authority Access Token %s " % e)
            logger.error("Auth server error {error}".format(error=e))
            auth_token["error"] = "Error in getting access token"
        else:
            error = auth_token.get("error", None)
            if error:
                logger.error("Authority server provided the following error during token request %s " % error)

        return auth_token

    def delete_operational_intent(self, dss_operational_intent_ref_id: str, ovn: str) -> DeleteOperationalIntentResponse:
        auth_token = self.get_auth_token()

        dss_opint_delete_url = self.dss_base_url + "dss/v1/operational_intent_references/" + dss_operational_intent_ref_id + "/" + ovn

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }
        # Send the entity ID and OVN
        delete_payload = DeleteOperationalIntentConstuctor(entity_id=dss_operational_intent_ref_id, ovn=ovn)

        dss_r = requests.delete(
            dss_opint_delete_url,
            json=json.loads(json.dumps(asdict(delete_payload))),
            headers=headers,
        )

        dss_response = dss_r.json()
        dss_r_status_code = dss_r.status_code

        if dss_r_status_code == 200:
            common_200_response = CommonDSS2xxResponse(message="Successfully deleted operational intent id %s" % dss_operational_intent_ref_id)
            dss_response_formatted = DeleteOperationalIntentResponseSuccess(
                subscribers=dss_response["subscribers"],
                operational_intent_reference=dss_response["operational_intent_reference"],
            )
            delete_op_int_status = DeleteOperationalIntentResponse(
                dss_response=dss_response_formatted,
                status=200,
                message=common_200_response,
            )
        elif dss_r_status_code == 404:
            common_400_response = CommonDSS4xxResponse(message="URL endpoint not found")
            delete_op_int_status = DeleteOperationalIntentResponse(dss_response=dss_response, status=404, message=common_400_response)

        elif dss_r_status_code == 409:
            common_400_response = CommonDSS4xxResponse(message="The provided ovn does not match the current version of existing operational intent")
            delete_op_int_status = DeleteOperationalIntentResponse(dss_response=dss_response, status=409, message=common_400_response)

        elif dss_r_status_code == 412:
            common_400_response = CommonDSS4xxResponse(
                message="The client attempted to delete the operational intent while marked as Down in the DSS"
            )
            delete_op_int_status = DeleteOperationalIntentResponse(dss_response=dss_response, status=412, message=common_400_response)
        else:
            common_400_response = CommonDSS4xxResponse(message="A error occurred while deleting the operational intent")
            delete_op_int_status = DeleteOperationalIntentResponse(dss_response=dss_response, status=500, message=common_400_response)
        return delete_op_int_status

    def get_and_process_nearby_operational_intents(self, volumes: List[Volume4D]) -> Union[dict, bool]:
        """This method processes the downloaded operational intents in to a GeoJSON object"""
        feat_collection = {"type": "FeatureCollection", "features": []}
        try:
            all_uss_op_int_details = self.get_nearby_operational_intents(volumes=volumes)
        except ConnectionError:
            raise ConnectionError("Could not reach peer USS for querying operational intent data")

        my_peer_uss_data_validator = PeerOperationalIntentValidator()
        all_received_intents_valid = my_peer_uss_data_validator.validate_nearby_operational_intents(nearby_operational_intents=all_uss_op_int_details)
        logger.info(
            "Validation processing completed for all received operational intents, result: {validation_status}".format(
                validation_status=all_received_intents_valid
            )
        )
        if not all_received_intents_valid:
            raise ValueError("Error in validating received data, cannot progress with processing")

        for uss_op_int_detail in all_uss_op_int_details:
            operational_intent_volumes = uss_op_int_detail.details.volumes
            my_volume_converter = VolumesConverter()
            my_volume_converter.convert_volumes_to_geojson(volumes=operational_intent_volumes)
            for f in my_volume_converter.geo_json["features"]:
                feat_collection["features"].append(f)

        return feat_collection

    def get_latest_airspace_volumes(self, volumes: List[Volume4D]) -> Union[list, List[OpInttoCheckDetails]]:
        # This method checks if a flight volume has conflicts with any other volume in the airspace
        all_opints_to_check = []
        try:
            all_uss_op_int_details = self.get_nearby_operational_intents(volumes=volumes)
        except ConnectionError:
            logger.info("Raising Connection Error 2")
            raise ConnectionError("Could not reach peer USS for querying operational intent data")

        my_peer_uss_data_validator = PeerOperationalIntentValidator()
        all_received_intents_valid = my_peer_uss_data_validator.validate_nearby_operational_intents(nearby_operational_intents=all_uss_op_int_details)
        logger.info(
            "Validation processing completed for all received operational intents (SCD), result: {validation_status}".format(
                validation_status=all_received_intents_valid
            )
        )
        if not all_received_intents_valid:
            raise ValueError("Error in validating received data, cannot progress with processing")

        for uss_op_int_detail in all_uss_op_int_details:
            if uss_op_int_detail.details.off_nominal_volumes:
                operational_intent_volumes = uss_op_int_detail.details.off_nominal_volumes
            else:
                operational_intent_volumes = uss_op_int_detail.details.volumes
            my_volume_converter = VolumesConverter()
            my_volume_converter.convert_volumes_to_geojson(volumes=operational_intent_volumes)
            minimum_rotated_rect = my_volume_converter.get_minimum_rotated_rectangle()
            cur_op_int_details = OpInttoCheckDetails(
                shape=minimum_rotated_rect,
                ovn=uss_op_int_detail.reference.ovn,
                id=uss_op_int_detail.reference.id,
            )
            all_opints_to_check.append(cur_op_int_details)

        return all_opints_to_check

    def notify_peer_uss_of_created_updated_operational_intent(
        self,
        uss_base_url: str,
        notification_payload: NotifyPeerUSSPostPayload,
        audience: str,
    ):
        """This method posts operational intent details to peer USS via a POST request to /uss/v1/operational_intents"""
        auth_token = self.get_auth_token(audience=audience)

        notification_url = uss_base_url + "/uss/v1/operational_intents"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        uss_r = requests.post(
            notification_url,
            json=json.loads(json.dumps(asdict(notification_payload))),
            headers=headers,
        )

        uss_r_status_code = uss_r.status_code

        if uss_r_status_code == 204:
            result_message = CommonDSS2xxResponse(message="Notified successfully")
            logger.info("Peer USS notified successfully")
        else:
            result_message = CommonDSS4xxResponse(message="Error in notification")
            logger.info(
                "Error in notifying peer USS at {endpoint}, the request resulted in a {uss_r_status_code} response from the peer".format(
                    endpoint=notification_url, uss_r_status_code=uss_r_status_code
                )
            )

        notification_result = USSNotificationResponse(status=uss_r_status_code, message=result_message)

        return notification_result

    def process_peer_uss_notifications(
        self,
        all_subscribers: List[SubscriberToNotify],
        operational_intent_details: OperationalIntentUSSDetails,
        operational_intent_reference: OperationalIntentReferenceDSSResponse,
        operational_intent_id: str,
    ):
        """This method sends a notification to all the subscribers of the operational intent reference in the DSS"""
        for subscriber in all_subscribers:
            domain_to_check = tldextract.extract(subscriber.uss_base_url)
            if domain_to_check.subdomain != "dummy" and domain_to_check.domain != "uss":
                operational_intent = OperationalIntentDetailsUSSResponse(reference=operational_intent_reference, details=operational_intent_details)

                notification_payload = NotifyPeerUSSPostPayload(
                    operational_intent_id=operational_intent_id, operational_intent=operational_intent, subscriptions=subscriber.subscriptions
                )
                audience = generate_audience_from_base_url(base_url=subscriber.uss_base_url)

                if audience != "host.docker.internal":
                    self.notify_peer_uss_of_created_updated_operational_intent(
                        uss_base_url=subscriber.uss_base_url, notification_payload=notification_payload, audience=audience
                    )

    def process_retrieved_airspace_volumes(
        self,
        current_network_opint_details_full: List[OpInttoCheckDetails],
        operational_intent_ref_id: str,
    ) -> List[OpInttoCheckDetails]:
        """The DSS returns all the volumes including ours, We dont need to check deconflicton for operation ID that we are updating, we therefore remove this from our deconfliction check and also update stored OVN"""

        all_existing_operational_intent_details = list(
            filter(
                lambda op_int_to_check: op_int_to_check.id != operational_intent_ref_id,
                current_network_opint_details_full,
            )
        )
        return all_existing_operational_intent_details

    def get_updated_ovn(
        self,
        current_network_opint_details_full: List[OpInttoCheckDetails],
        operational_intent_ref_id: str,
    ) -> Union[None, str]:
        """This method gets the latest ovn from the dss for the specified operational intent reference"""

        updated_ovn = next(
            (
                current_network_opint_detail.ovn
                for current_network_opint_detail in current_network_opint_details_full
                if current_network_opint_detail.id == operational_intent_ref_id
            ),
            None,
        )

        return updated_ovn

    def generate_airspace_keys(self, current_network_opint_details_full: List[OpInttoCheckDetails]) -> List[str]:
        airspace_keys = []
        for cur_op_int_detail in current_network_opint_details_full:
            airspace_keys.append(cur_op_int_detail.ovn)
        return airspace_keys

    def check_extents_conflict_with_latest_volumes(
        self,
        all_existing_operational_intent_details: List[OpInttoCheckDetails],
        extents: List[Volume4D],
    ) -> bool:
        my_ind_volumes_converter = VolumesConverter()
        my_ind_volumes_converter.convert_volumes_to_geojson(volumes=extents)
        ind_volumes_polygon = my_ind_volumes_converter.get_minimum_rotated_rectangle()
        is_conflicted = rtree_helper.check_polygon_intersection(
            op_int_details=all_existing_operational_intent_details,
            polygon_to_check=ind_volumes_polygon,
        )

        return is_conflicted

    def check_if_update_payload_should_be_submitted_to_dss(
        self, current_state: str, new_state: str, extents_conflict_with_dss_volumes: bool, priority: int
    ) -> ShouldSendtoDSSProcessingResponse:
        should_opint_be_sent_to_dss = ShouldSendtoDSSProcessingResponse(
            should_submit_update_payload_to_dss=0,
            check_id=OpIntUpdateCheckResultCodes.Z,
            tentative_flight_plan_processing_response=FlightPlanCurrentStatus.Processing,
        )
        if current_state == "Activated" and new_state == "Activated" and extents_conflict_with_dss_volumes:
            logger.debug("Case B")
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = 0
            should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.B
            should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.OkToFly

        elif current_state == "Activated" or new_state in ["Nonconforming", "Contingent"]:
            logger.debug("Case A")
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = 1
            should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.A
            should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.OffNominal
        elif current_state == "Activated" and new_state == "Activated":
            logger.debug("Case C")
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = 1
            should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.C
            should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.OkToFly
        elif priority == 100:
            logger.debug("Case D")
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = 1
            should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.D
        else:
            submit_update_payload_to_dss = 0 if extents_conflict_with_dss_volumes else 1
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = submit_update_payload_to_dss
            if should_opint_be_sent_to_dss:
                should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.E
                should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.Planned
            else:
                should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.F
                should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.NotPlanned

        logger.info("Update payload check complete..")

        return should_opint_be_sent_to_dss

    def update_specified_operational_intent_reference(
        self,
        operational_intent_ref_id: str,
        extents: List[Volume4D],
        current_state: str,
        new_state: str,
        subscription_id: str,
        ovn: str,
        deconfliction_check=False,
        priority: int = 0,
    ) -> OperationalIntentUpdateResponse:
        """
        Update a specified operational intent reference in the DSS.
        Args:
            operational_intent_ref_id (str): The ID of the operational intent reference to update.
            extents (List[Volume4D]): The list of 4D volumes defining the operational intent.
            current_state (str): The current state of the operational intent.
            new_state (str): The new state to update the operational intent to.
            ovn (str): The operational volume number.
            subscription_id (str): The subscription ID associated with the operational intent.
            deconfliction_check (bool, optional): Flag to indicate if deconfliction check is required. Defaults to False.
            priority (int, optional): The priority of the update. Defaults to 0.
        Returns:
            Optional[OperationalIntentUpdateResponse]: The response of the update operation, or None if the update is not submitted.
        """
        auth_token = self.get_auth_token()
        logger.info("Updating operational intent reference: {operational_intent_ref_id}".format(operational_intent_ref_id=operational_intent_ref_id))
        flight_blender_base_url = env.get("FLIGHTBLENDER_FQDN", "http://localhost:8000")

        # Initialize the update request with empty airspace key
        operational_intent_update_payload = OperationalIntentUpdateRequest(
            extents=extents,
            state=new_state,
            uss_base_url=flight_blender_base_url,
            subscription_id=subscription_id,
            key=[],
        )
        # Get the latest airspace volumes
        try:
            current_network_opint_details_full = self.get_latest_airspace_volumes(volumes=extents)
        except ValueError:
            # Update unsuccessful, problems with processing peer USS volumes
            d_r = CommonPeer9xxResponse(message="Error in validating received operational intents from peer USS")
            message = "Error in updating operational intent in the DSS, peer USS shared invalid data"
            opint_update_result = OperationalIntentUpdateResponse(dss_response=d_r, status=999, message=message)
            return opint_update_result
        except ConnectionError:
            logger.info("Raising Connection Error 3")
            logger.info("Connection error with peer USS, cannot update volume...")
            # Update unsuccessful, problems with processing peer USS volumes
            d_r = CommonPeer9xxResponse(message="Error in validating received operational intents from peer USS")
            message = "Error in updating operational intent in the DSS, peer USS shared invalid data"
            opint_update_result = OperationalIntentUpdateResponse(dss_response=d_r, status=408, message=message)
            return opint_update_result

        all_existing_operational_intent_details = self.process_retrieved_airspace_volumes(
            current_network_opint_details_full=current_network_opint_details_full,
            operational_intent_ref_id=operational_intent_ref_id,
        )
        latest_ovn = self.get_updated_ovn(
            current_network_opint_details_full=current_network_opint_details_full,
            operational_intent_ref_id=operational_intent_ref_id,
        )
        updated_ovn = latest_ovn if latest_ovn else ovn
        airspace_keys = self.generate_airspace_keys(current_network_opint_details_full=current_network_opint_details_full)
        operational_intent_update_payload.key = airspace_keys
        if all_existing_operational_intent_details:
            extents_conflict_with_dss_volumes = self.check_extents_conflict_with_latest_volumes(
                all_existing_operational_intent_details=all_existing_operational_intent_details,
                extents=extents,
            )
        else:
            extents_conflict_with_dss_volumes = False

        pre_submission_checks = self.check_if_update_payload_should_be_submitted_to_dss(
            current_state=current_state,
            new_state=new_state,
            extents_conflict_with_dss_volumes=extents_conflict_with_dss_volumes,
            priority=priority,
        )

        if not pre_submission_checks.should_submit_update_payload_to_dss:
            d_r = None
            dss_r_status_code = 999
            message = "Update to flight will not be processed, will not be submitting to DSS"
            opint_update_result = OperationalIntentUpdateResponse(
                dss_response=d_r, status=dss_r_status_code, message=message, additional_information=pre_submission_checks
            )
            return opint_update_result

        dss_opint_update_url = self.dss_base_url + "dss/v1/operational_intent_references/" + operational_intent_ref_id + "/" + updated_ovn
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        flight_blender_base_url = env.get("FLIGHTBLENDER_FQDN", "http://flight-blender:8000")
        dss_r = requests.put(
            dss_opint_update_url,
            json=json.loads(json.dumps(asdict(operational_intent_update_payload))),
            headers=headers,
        )
        dss_response = dss_r.json()
        dss_r_status_code = dss_r.status_code

        if dss_r_status_code == 200:
            # Update request was successful, notify the subscribers
            subscribers = dss_response["subscribers"]
            all_subscribers: List[SubscriberToNotify] = []
            for subscriber in subscribers:
                subscriptions = subscriber["subscriptions"]
                uss_base_url = subscriber["uss_base_url"]
                if uss_base_url != flight_blender_base_url:
                    all_subscription_states: List[SubscriptionState] = []
                    for subscription in subscriptions:
                        s_state = SubscriptionState(
                            subscription_id=subscription["subscription_id"],
                            notification_index=subscription["notification_index"],
                        )
                        all_subscription_states.append(s_state)
                    subscriber_obj = SubscriberToNotify(subscriptions=all_subscription_states, uss_base_url=uss_base_url)
                    all_subscribers.append(subscriber_obj)
            my_op_int_ref_helper = OperationalIntentReferenceHelper()
            operational_intent_reference: OperationalIntentReferenceDSSResponse = my_op_int_ref_helper.parse_operational_intent_reference_from_dss(
                operational_intent_reference=dss_response["operational_intent_reference"]
            )
            d_r = OperationalIntentUpdateSuccessResponse(
                subscribers=all_subscribers,
                operational_intent_reference=operational_intent_reference,
            )
            logger.info("Updated Operational Intent in the DSS successfully...")

            message = CommonDSS4xxResponse(message="Successfully updated operational intent")
            opint_update_result = OperationalIntentUpdateResponse(dss_response=d_r, status=dss_r_status_code, message=message)
            return opint_update_result

        elif dss_r_status_code in [400, 401, 403, 409, 412, 413, 429]:
            # Update unsuccessful
            d_r = OperationalIntentUpdateErrorResponse(message=dss_response["message"])
            message = CommonDSS4xxResponse(message="Error in updating operational intent in the DSS")
            opint_update_result = OperationalIntentUpdateResponse(dss_response=d_r, status=dss_r_status_code, message=message)
            return opint_update_result

    def create_and_submit_operational_intent_reference(
        self,
        state: str,
        priority: str,
        volumes: List[Volume4D],
        off_nominal_volumes: List[Volume4D],
    ) -> OperationalIntentSubmissionStatus:
        """
        Create and submit an operational intent reference to the DSS (Discovery and Synchronization Service).
        This function creates a new operational intent reference, checks for conflicts with existing operational intents,
        and submits the new operational intent to the DSS if no conflicts are found.
        Args:
            state (str): The state of the operational intent (e.g., "Accepted", "Activated").
            priority (str): The priority level of the operational intent.
            volumes (List[Volume4D]): A list of 4D volumes defining the operational intent's airspace.
            off_nominal_volumes (List[Volume4D]): A list of 4D volumes defining off-nominal airspace.
        Returns:
            OperationalIntentSubmissionStatus: The status of the operational intent submission, including success or failure details.
        """
        auth_token = self.get_auth_token()
        logger.info("Creating new operational intent...")

        # A token from authority was received, we can now submit the operational intent
        new_entity_id = str(uuid.uuid4())
        new_operational_intent_ref_creation_url = self.dss_base_url + "dss/v1/operational_intent_references/" + new_entity_id
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }
        management_key = str(uuid.uuid4())
        airspace_keys = []
        flight_blender_base_url = env.get("FLIGHTBLENDER_FQDN", "http://flight-blender:8000")
        implicit_subscription_parameters = ImplicitSubscriptionParameters(uss_base_url=flight_blender_base_url)
        operational_intent_reference = OperationalIntentReference(
            extents=volumes,
            key=airspace_keys,
            state=state,
            uss_base_url=flight_blender_base_url,
            new_subscription=implicit_subscription_parameters,
        )
        d_r = OperationalIntentSubmissionStatus(
            status="not started",
            status_code=503,
            message="Service is not available / connection not established",
            dss_response={},
            operational_intent_id=new_entity_id,
        )
        # Query other USSes for operational intent
        # Check if there are conflicts (or not)
        logger.info("Checking flight de-confliction status...")
        # Get all operational intents in the area

        try:
            all_existing_operational_intent_details = self.get_latest_airspace_volumes(volumes=volumes)
        except ValueError:
            logger.info("Error in processing peer USS data, cannot create a new operational intent..")
            d_r = OperationalIntentSubmissionStatus(
                status="peer_uss_data_sharing_issue",
                status_code=900,
                message="Error in processing peer USS data, cannot create a new operational intent",
                dss_response={},
                operational_intent_id="",
            )
            return d_r

        except ConnectionError:
            logger.info("Raising Connection Error 4")
            logger.info("Error in processing peer USS data, cannot create a new operational intent..")
            d_r = OperationalIntentSubmissionStatus(
                status="peer_uss_data_sharing_issue",
                status_code=408,
                message="Error in processing peer USS data, cannot create a new operational intent",
                dss_response={},
                operational_intent_id="",
            )
            return d_r

        if isinstance(all_existing_operational_intent_details, list):
            logger.info(
                "Found {all_existing_operational_intent_details:02} operational intent references in the DSS".format(
                    all_existing_operational_intent_details=len(all_existing_operational_intent_details)
                )
            )
        else:
            logger.info("No operational intent references found in the DSS")

        if all_existing_operational_intent_details:
            if isinstance(all_existing_operational_intent_details, list):
                logger.info(
                    "Checking deconfliction status with {num_existing_op_ints:02} operational intent details".format(
                        num_existing_op_ints=len(all_existing_operational_intent_details)
                    )
                )
            else:
                logger.info("No operational intent details to check for deconfliction.")
            my_ind_volumes_converter = VolumesConverter()
            my_ind_volumes_converter.convert_volumes_to_geojson(volumes=volumes)
            ind_volumes_polygon = my_ind_volumes_converter.get_minimum_rotated_rectangle()

            for cur_op_int_detail in all_existing_operational_intent_details:
                airspace_keys.append(cur_op_int_detail.ovn)

            if priority == 100:
                deconflicted = True
            else:
                airspace_keys.append(management_key)
                is_conflicted = rtree_helper.check_polygon_intersection(
                    op_int_details=all_existing_operational_intent_details,
                    polygon_to_check=ind_volumes_polygon,
                )
                deconflicted = False if is_conflicted else True
        else:
            deconflicted = True
            logger.info("No existing operational intent references in the DSS, deconfliction status: %s" % deconflicted)
        # logger.info("Airspace keys: %s" % airspace_keys)
        operational_intent_reference.keys = airspace_keys
        # logger.info("Deconfliction status: %s" % deconflicted)
        # logger.info("Flight deconfliction status checked")
        opint_creation_payload = json.loads(json.dumps(asdict(operational_intent_reference)))
        dss_response = {}

        if deconflicted:
            try:
                dss_r = requests.put(
                    new_operational_intent_ref_creation_url,
                    json=opint_creation_payload,
                    headers=headers,
                )
            except Exception as re:
                logger.error("Error in putting operational intent in the DSS %s " % re)
                d_r = OperationalIntentSubmissionStatus(
                    status="failure",
                    status_code=500,
                    message=re,
                    dss_response={},
                    operational_intent_id=new_entity_id,
                )
                dss_r_status_code = d_r.status_code
                dss_response = {"error": re}
            else:
                dss_response = dss_r.json()
                dss_r_status_code = dss_r.status_code

            if dss_r_status_code == 201:
                subscribers = dss_response["subscribers"]
                all_subscribers_to_notify = []
                for s in subscribers:
                    subs = s["subscriptions"]
                    all_subs = []
                    for subscription in subs:
                        s_s = SubscriptionState(
                            subscription_id=subscription["subscription_id"],
                            notification_index=subscription["notification_index"],
                        )
                        all_subs.append(s_s)
                    subscriber_to_notify = SubscriberToNotify(subscriptions=all_subs, uss_base_url=s["uss_base_url"])
                    all_subscribers_to_notify.append(subscriber_to_notify)

                o_i_r = dss_response["operational_intent_reference"]
                my_op_int_ref_helper = OperationalIntentReferenceHelper()
                operational_intent_r: OperationalIntentReferenceDSSResponse = my_op_int_ref_helper.parse_operational_intent_reference_from_dss(
                    operational_intent_reference=o_i_r
                )
                dss_creation_response = OperationalIntentSubmissionSuccess(
                    operational_intent_reference=operational_intent_r,
                    subscribers=all_subscribers_to_notify,
                )
                logger.info("Successfully created operational intent in the DSS")
                logger.debug("Response details from the DSS %s" % dss_r.text)
                d_r = OperationalIntentSubmissionStatus(
                    status="success",
                    status_code=201,
                    message="Successfully created operational intent in the DSS",
                    dss_response=dss_creation_response,
                    operational_intent_id=new_entity_id,
                )
            elif dss_r_status_code in [400, 401, 403, 409, 43, 429]:
                dss_creation_response_error = OperationalIntentSubmissionError(result=dss_response, notes=dss_r.text)
                logger.error("DSS operational intent reference creation error %s" % dss_r.text)
                d_r = OperationalIntentSubmissionStatus(
                    status="failure",
                    status_code=dss_r_status_code,
                    message=dss_r.text,
                    dss_response=dss_creation_response_error,
                    operational_intent_id=new_entity_id,
                )

            else:
                d_r.status_code = dss_r_status_code
                d_r.dss_response = dss_response
                logger.error("Error submitting operational intent to the DSS: %s" % dss_response)
        else:
            # When flight is not deconflicted, Flight Blender assigns a error code of 500
            logger.info("Flight not deconflicted, there are other flights in the area..")
            d_r = OperationalIntentSubmissionStatus(
                status="conflict_with_flight",
                status_code=500,
                message="Flight not deconflicted, there are other flights in the area",
                dss_response={},
                operational_intent_id="",
            )

        return d_r
