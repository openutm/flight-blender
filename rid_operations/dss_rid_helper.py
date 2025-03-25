## A module to read data from a DSS, this specifically implements the Remote ID standard as released on Oct-2020
## For more information review: https://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/uastech/standards/astm_rid_1.0/remoteid/canonical.yaml
## and this diagram https://github.com/interuss/dss/blob/master/assets/generated/rid_display.png

import hashlib
import json
import logging
import math
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from os import environ as env
from typing import List, Union

import requests
import tldextract
from dacite import from_dict
from dotenv import find_dotenv, load_dotenv
from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon, box
from uas_standards.astm.f3411.v22a.constants import (
    NetMinClusterSizePercent,
    NetMinObfuscationDistanceM,
)

from auth_helper import dss_auth_helper
from auth_helper.common import get_redis
from common.auth_token_audience_helper import generate_audience_from_base_url
from common.data_definitions import RESPONSE_CONTENT_TYPE
from common.database_operations import FlightBlenderDatabaseWriter
from flight_feed_operations.data_definitions import SingleAirtrafficObservation
from rid_operations.data_definitions import (
    UASID,
    Altitude,
    LatLngPoint,
    OperatorLocation,
    UAClassificationEU,
)

from .rid_utils import (
    Cluster,
    ClusterDetail,
    ClusterPosition,
    IdentificationServiceArea,
    ISACreationRequest,
    ISACreationResponse,
    RIDAltitude,
    RIDAuthData,
    RIDFlight,
    RIDFlightDetails,
    RIDFlightsRecord,
    RIDLatLngPoint,
    RIDOperatorDetails,
    RIDPolygon,
    RIDSubscription,
    RIDTime,
    RIDVolume3D,
    RIDVolume4D,
    SubscriberToNotify,
    SubscriptionResponse,
    SubscriptionState,
    Volume4D,
)

logger = logging.getLogger("django")
load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


geod = Geod(ellps="WGS84")


class RemoteIDOperations:
    def __init__(self):
        self.dss_base_url = env.get("DSS_BASE_URL", "000")
        self.r = get_redis()

    def compute_polygon_area(self, polygon: Polygon):
        poly_area_m2, poly_perimeter = geod.geometry_area_perimeter(polygon)

        return poly_area_m2

    def extend_cluster(self, view_area_sqm: float, min_x: float, min_y: float, max_x: float, max_y: float, all_positions: list[Point]) -> Cluster:
        """Code from InterUSS monitoring mocks"""

        cluster = Cluster(
            x_min=min_x,
            x_max=max_x,
            y_min=min_y,
            y_max=max_y,
            points=all_positions,
        )

        cluster_width = geod.geometry_length(LineString([Point(min_x, min_y), Point(max_x, min_y)]))
        cluster_height = geod.geometry_length(LineString([Point(min_x, min_y), Point(min_x, max_y)]))
        cluster_area = cluster_width * cluster_height

        # Extend cluster width to match the minimum distance required by NET0490
        if cluster_width < 2 * NetMinObfuscationDistanceM:
            delta = NetMinObfuscationDistanceM - cluster_width / 2
            cluster = Cluster(
                x_min=min_x - delta,
                x_max=max_x + delta,
                y_min=min_y,
                y_max=max_y,
                points=all_positions,
            )

        # Extend cluster height to match the minimum distance required by NET0490
        if cluster_height < 2 * NetMinObfuscationDistanceM:
            delta = NetMinObfuscationDistanceM - cluster_height / 2
            cluster = Cluster(
                x_min=min_x,
                x_max=max_x,
                y_min=min_y - delta,
                y_max=max_y + delta,
                points=all_positions,
            )

        # Extend cluster to the minimum area size required by NET0480
        min_cluster_area = view_area_sqm * NetMinClusterSizePercent / 100

        if cluster_area < min_cluster_area:
            scale = math.sqrt(min_cluster_area / cluster_area) / 2
            cluster = Cluster(
                x_min=min_x - scale * cluster_width,
                x_max=max_x + scale * cluster_width,
                y_min=min_y - scale * cluster_height,
                y_max=max_y + scale * cluster_height,
                points=all_positions,
            )

        return cluster

    def generate_cluster_details(self, rid_flights: List[RIDFlight], view_box: Polygon) -> List[ClusterDetail]:
        all_positions: list[Point] = []

        view_min = view_box.bounds[0:2]
        view_max = view_box.bounds[2:4]

        view_min_point = Point(view_min[0], view_min[1])
        view_max_point = Point(view_max[0], view_max[1])
        all_positions.append(view_min_point)
        all_positions.append(view_max_point)

        for rid_flight in rid_flights:
            flight_most_recent_position = rid_flight.most_recent_position
            position = Point(flight_most_recent_position.lat, flight_most_recent_position.lng)
            all_positions.append(position)

        min_x, min_y, max_x, max_y = all_positions[0].bounds
        for position in all_positions[1:]:
            x_min, y_min, x_max, y_max = position.bounds
            min_x = min(min_x, x_min)
            min_y = min(min_y, y_min)
            max_x = max(max_x, x_max)
            max_y = max(max_y, y_max)

        # bounding_box_polygon = box(min_x, min_y, max_x, max_y)
        bounding_box_area_sq_meters = self.compute_polygon_area(view_box)

        extended_cluster = self.extend_cluster(
            view_area_sqm=bounding_box_area_sq_meters,
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
            all_positions=all_positions,
        )
        number_of_flights = len(rid_flights)
        cluster = ClusterDetail(
            corners=[
                ClusterPosition(lat=extended_cluster.y_min, lng=extended_cluster.x_min),
                ClusterPosition(lat=extended_cluster.y_max, lng=extended_cluster.x_max),
            ],
            area_sqm=bounding_box_area_sq_meters,
            number_of_flights=number_of_flights,
        )
        cluster_details = [cluster]

        return cluster_details

    def create_dss_isa(
        self,
        flight_extents: Union[RIDVolume4D, Volume4D],
        uss_base_url: str,
        expiration_time_seconds: int = 30,
    ) -> ISACreationResponse:
        """This method PUTS /dss/subscriptions"""
        isa_creation_response = ISACreationResponse(created=False, service_area=None, subscribers=[])
        new_isa_id = str(uuid.uuid4())

        my_authorization_helper = dss_auth_helper.AuthorityCredentialsGetter()
        audience = env.get("DSS_SELF_AUDIENCE", "000")
        error = None

        try:
            assert audience
        except AssertionError:
            logger.error("Error in getting Authority Access Token DSS_SELF_AUDIENCE is not set in the environment")
            return isa_creation_response

        try:
            auth_token = my_authorization_helper.get_cached_credentials(audience=audience, token_type="rid")
        except Exception as e:
            logger.error("Error in getting Authority Access Token %s " % e)
            return isa_creation_response
        else:
            error = auth_token.get("error")

        try:
            assert error is None
        except AssertionError:
            return isa_creation_response
        else:
            # A token from authority was received,

            dss_isa_create_url = self.dss_base_url + "rid/v2/dss/identification_service_areas/" + new_isa_id

            # check if a subscription already exists for this view_port

            headers = {
                "content-type": RESPONSE_CONTENT_TYPE,
                "Authorization": "Bearer " + auth_token["access_token"],
            }
            p = ISACreationRequest(extents=flight_extents, uss_base_url=uss_base_url)
            p_dict = asdict(p)
            try:
                dss_r = requests.put(
                    dss_isa_create_url,
                    json=json.loads(json.dumps(p_dict)),
                    headers=headers,
                )
            except Exception as re:
                logger.error("Error in posting to DSS URL %s " % re)
                return isa_creation_response

            try:
                assert dss_r.status_code == 200
                isa_creation_response.created = 1
            except AssertionError:
                logger.error("Error in creating ISA in the DSS %s" % dss_r.text)
                return isa_creation_response
            else:
                dss_response = dss_r.json()
                dss_response_service_area = dss_response["service_area"]
                service_area = IdentificationServiceArea(
                    uss_base_url=dss_response_service_area["uss_base_url"],
                    owner=dss_response_service_area["owner"],
                    time_start=RIDTime(
                        value=dss_response_service_area["time_start"]["value"],
                        format=dss_response_service_area["time_start"]["format"],
                    ),
                    time_end=RIDTime(
                        value=dss_response_service_area["time_end"]["value"],
                        format=dss_response_service_area["time_end"]["format"],
                    ),
                    version=dss_response_service_area["version"],
                    id=dss_response_service_area["id"],
                )

                dss_response_subscribers = dss_response["subscribers"]

                dss_r_subs: List[SubscriberToNotify] = []
                for subscriber in dss_response_subscribers:
                    subs = subscriber["subscriptions"]
                    all_s = []
                    for sub in subs:
                        s = SubscriptionState(
                            subscription_id=sub["subscription_id"],
                            notification_index=sub["notification_index"],
                        )
                        all_s.append(asdict(s))

                    subscriber_to_notify = SubscriberToNotify(url=subscriber["url"], subscriptions=all_s)
                    dss_r_subs.append(subscriber_to_notify)

                for subscriber in dss_r_subs:
                    url = "{}/uss/identification_service_areas/{}".format(subscriber.url, new_isa_id)
                    try:
                        ext = tldextract.extract(subscriber.url)
                    except Exception:
                        uss_audience = "localhost"
                    else:
                        if ext.domain in [
                            "localhost",
                            "internal",
                            "localutm",
                        ]:  # for host.docker.internal type calls
                            uss_audience = "localhost"
                        else:
                            uss_audience = ".".join(ext[:3])  # get the subdomain, domain and suffix and create a audience and get credentials

                    # Notify subscribers
                    payload = {
                        "service_area": asdict(service_area),
                        "subscriptions": subscriber.subscriptions,
                        "extents": json.loads(json.dumps(asdict(flight_extents))),
                    }

                    auth_credentials = my_authorization_helper.get_cached_credentials(audience=uss_audience, token_type="rid")
                    headers = {
                        "content-type": RESPONSE_CONTENT_TYPE,
                        "Authorization": "Bearer " + auth_credentials["access_token"],
                    }
                    try:
                        response = requests.post(url, headers=headers, json=json.loads(json.dumps(payload)))
                    except Exception as re:
                        logger.error("Error in sending subscriber notification to %s :  %s " % (url, re))
                    if response.status_code == 204:
                        logger.info(response.status_code)
                        logger.info("Successfully notified subscriber %s" % url)

                logger.info("Successfully created a DSS ISA %s" % new_isa_id)
                # iterate over the service areas to get flights URL to poll
                isa_key = "isa-" + service_area.id
                isa_seconds_timedelta = timedelta(seconds=expiration_time_seconds)
                self.r.set(isa_key, 1)
                self.r.expire(name=isa_key, time=isa_seconds_timedelta)
                isa_creation_response.created = 1
                isa_creation_response.service_area = service_area
                isa_creation_response.subscribers = dss_r_subs

                return asdict(isa_creation_response)

    def create_dss_subscription(
        self, vertex_list: list, view: str, request_uuid, subscription_duration_seconds: int = 30, is_simulated: bool = False
    ) -> SubscriptionResponse:
        """This method PUTS /dss/subscriptions"""
        subscription_response = SubscriptionResponse(created=False, dss_subscription_id=None, notification_index=0)

        my_authorization_helper = dss_auth_helper.AuthorityCredentialsGetter()
        audience = env.get("DSS_SELF_AUDIENCE", "000")
        error = None

        try:
            assert audience
        except AssertionError:
            logger.error("Error in getting Authority Access Token DSS_SELF_AUDIENCE is not set in the environment")
            return subscription_response

        try:
            auth_token = my_authorization_helper.get_cached_credentials(audience=audience, token_type="rid")
        except Exception as e:
            logger.error("Error in getting Authority Access Token %s " % e)
            return subscription_response
        else:
            error = auth_token.get("error")

        try:
            assert error is None
        except AssertionError:
            return subscription_response
        else:
            # A token from authority was received,
            new_subscription_id = str(uuid.uuid4())
            dss_subscription_url = self.dss_base_url + "rid/v2/dss/subscriptions/" + new_subscription_id
            # check if a subscription already exists for this view_port

            now = datetime.now()
            # callback_url = env.get("FLIGHTBLENDER_FQDN", "https://www.https://www.flightblender.com") + "/dss/identification_service_areas"

            # callback_url += "/" + new_subscription_id

            uss_base_url = env.get("FLIGHTBLENDER_FQDN", "https://www.https://www.flightblender.com") + "/rid"

            subscription_seconds_timedelta = timedelta(seconds=subscription_duration_seconds)
            current_time = now.isoformat() + "Z"
            fifteen_seconds_from_now = now + subscription_seconds_timedelta
            fifteen_seconds_from_now_isoformat = fifteen_seconds_from_now.isoformat() + "Z"
            headers = {
                "content-type": RESPONSE_CONTENT_TYPE,
                "Authorization": "Bearer " + auth_token["access_token"],
            }

            lat_lng_list = [RIDLatLngPoint(lat=v["lat"], lng=v["lng"]) for v in vertex_list]

            isa_polygon = RIDPolygon(vertices=lat_lng_list)
            volume_three_d = RIDVolume3D(
                outline_polygon=isa_polygon,
                altitude_lower=RIDAltitude(value=0.5, reference="W84", units="M"),
                altitude_upper=RIDAltitude(value=800, reference="W84", units="M"),
            )
            time_start = RIDTime(value=current_time, format="RFC3339")
            time_end = RIDTime(value=fifteen_seconds_from_now_isoformat, format="RFC3339")

            volume_object = RIDVolume4D(volume=volume_three_d, time_start=time_start, time_end=time_end)

            payload = {
                "extents": asdict(volume_object),
                "uss_base_url": uss_base_url,
            }

            try:
                dss_r = requests.put(dss_subscription_url, json=payload, headers=headers)
            except Exception as re:
                logger.error("Error in posting to subscription URL %s " % re)
                return subscription_response

            try:
                assert dss_r.status_code == 200
                subscription_response.created = True
            except AssertionError:
                logger.error("Error in creating subscription in the DSS %s" % dss_r.text)
                return subscription_response
            else:
                dss_response = dss_r.json()

                service_areas = dss_response["service_areas"]
                dss_subscription_details = dss_response["subscription"]

                dss_subscription_details = from_dict(data_class=RIDSubscription, data=dss_response["subscription"])
                subscription_id = dss_subscription_details.id
                notification_index = dss_subscription_details.notification_index
                subscription_response.notification_index = notification_index
                subscription_response.dss_subscription_id = subscription_id
                # logger.info("Successfully created a DSS subscription ID %s" % subscription_id)
                # iterate over the service areas to generatio flights URL to poll

                flights_dict = RIDFlightsRecord(service_areas=service_areas, subscription=dss_subscription_details)

                view_hash = int(hashlib.sha256(view.encode("utf-8")).hexdigest(), 16) % 10**8

                my_database_writer = FlightBlenderDatabaseWriter()
                my_database_writer.create_rid_subscription_record(
                    subscription_id=subscription_id,
                    record_id=request_uuid,
                    view_hash=view_hash,
                    end_datetime=fifteen_seconds_from_now_isoformat,
                    is_simulated=is_simulated,
                    view=view,
                    flights_dict=json.dumps(asdict(flights_dict, dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)})),
                )

                return subscription_response

    def delete_dss_subscription(self, subscription_id: str):
        """This module calls the DSS to delete a subscription"""

        pass

    def query_uss_for_rid_details(self, rid_flight_details_query_url: str, flight_id: str, headers: dict):
        """
        Queries the USS (UAS Service Supplier) for Remote ID (RID) flight details and stores the details in Redis.
        Args:
            rid_flight_details_query_url (str): The URL to query the USS for RID flight details.
            flight_id (str): The unique identifier for the flight.
            headers (dict): The headers to include in the request to the USS.
        Returns:
            None
        Raises:
            requests.exceptions.RequestException: If there is an issue with the HTTP request to the USS.
            KeyError: If the expected keys are not found in the response from the USS.
        Notes:
            - The flight details are stored in Redis with a key in the format "flight_details:<flight_id>".
            - The stored flight details expire after 5 minutes (3000 seconds).
        """

        flight_details_storage = "flight_details:" + flight_id
        if not self.r.exists(flight_details_storage):
            # Get and store the flight details
            flight_details_request = requests.get(rid_flight_details_query_url, headers=headers)
            if flight_details_request.status_code != 200:
                logger.info("Error in retrieving flight details for %s" % flight_id)
                logger.error(flight_details_request.text)
                return

            _fd_raw = flight_details_request.json()
            fd = _fd_raw["details"]

            logger.info("Retrieved Flight Details for %s" % flight_id)
            operation_description = None
            if "operation_description" in fd.keys():
                operation_description = fd["operation_description"]
            operator_id = None
            if "operator_id" in fd.keys():
                operator_id = fd["operator_id"]
            operator_location = None
            if "operator_location" in fd.keys():
                operator_location = from_dict(data_class=OperatorLocation, data=fd["operator_location"])
            auth_data = None
            if "auth_data" in fd.keys():
                auth_data = from_dict(data_class=RIDAuthData, data=fd["auth_data"])

            uas_id = None
            if "uas_id" in fd.keys():
                uas_id = from_dict(data_class=UASID, data=fd["uas_id"])

            eu_classification = None
            if fd.get("eu_classification"):
                eu_classification = from_dict(data_class=UAClassificationEU, data=fd["eu_classification"])

            flight_detail = RIDFlightDetails(
                id=flight_id,
                operation_description=operation_description,
                operator_location=operator_location,
                operator_id=operator_id,
                auth_data=auth_data,
                uas_id=uas_id,
                eu_classification=eu_classification,
            )

            self.r.set(flight_details_storage, json.dumps(asdict(flight_detail)))

            self.r.expire(flight_details_storage, time=1200)

    def query_uss_for_rid(self, flight_details: str, subscription_id: str, view: str):
        _flight_details = from_dict(data_class=RIDFlightsRecord, data=json.loads(flight_details))

        my_database_writer = FlightBlenderDatabaseWriter()
        authority_credentials = dss_auth_helper.AuthorityCredentialsGetter()

        all_flights_url = []
        for _service_area in _flight_details.service_areas:
            rid_query_url = _service_area.uss_base_url + "/uss/flights" + "?view=" + view

            logger.debug("Flight url list : %s" % all_flights_url)
            audience = generate_audience_from_base_url(base_url=_service_area.uss_base_url)

            auth_credentials = authority_credentials.get_cached_credentials(audience=audience, token_type="rid")
            headers = {
                "content-type": RESPONSE_CONTENT_TYPE,
                "Authorization": "Bearer " + auth_credentials["access_token"],
            }
            flights_request = requests.get(rid_query_url, headers=headers)

            if flights_request.status_code == 200:
                # https://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/uastech/standards/dd4016b09fc8cb98f30c2a17b5a088fb2995ab54/remoteid/canonical.yaml
                flights_response = flights_request.json()

                all_flights = flights_response["flights"]
                for flight in all_flights:
                    flight_id = flight["id"]

                    rid_flight_details_query_url = f"{_service_area.uss_base_url}/uss/flights/{flight_id}/details"

                    self.query_uss_for_rid_details(rid_flight_details_query_url=rid_flight_details_query_url, flight_id=flight_id, headers=headers)

                    try:
                        assert flight.get("current_state") is not None
                    except AssertionError:
                        logger.error("There is no current_state provided by SP on the flights url %s" % rid_query_url)
                        logger.debug(json.dumps(flight))
                    else:
                        flight_current_state = flight["current_state"]
                        position = flight_current_state["position"]

                        recent_positions = flight["recent_positions"] if "recent_positions" in flight.keys() else []

                        flight_metadata = {
                            "id": flight_id,
                            "simulated": flight["simulated"],
                            "aircraft_type": flight["aircraft_type"],
                            "subscription_id": subscription_id,
                            "current_state": flight_current_state,
                            "recent_positions": recent_positions,
                        }
                        # logger.info("Writing flight remote-id data..")
                        if {"lat", "lng", "alt"} <= position.keys():
                            # check if lat / lng / alt existis
                            single_observation = {
                                "session_id": subscription_id,
                                "icao_address": flight_id,
                                "traffic_source": 11,
                                "source_type": 1,
                                "lat_dd": position["lat"],
                                "lon_dd": position["lng"],
                                "altitude_mm": position["alt"],
                                "metadata": flight_metadata,
                            }
                            single_observation = from_dict(data_class=SingleAirtrafficObservation, data=single_observation)
                            logger.debug("Writing flight remote-id data..")
                            my_database_writer.write_flight_observation(single_observation=single_observation)

                        else:
                            logger.error("Error in received flights data: %{url}s ".format(**flight))

            else:
                logs_dict = {
                    "url": cur_flight_url,
                    "status_code": flights_request.status_code,
                }
                logger.info("Received a non 200 error from {url} : {status_code} ".format(**logs_dict))
                logger.info("Detailed Response %s" % flights_request.text)
