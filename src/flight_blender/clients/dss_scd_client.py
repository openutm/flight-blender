import asyncio
import json
import uuid
from dataclasses import asdict
from typing import Any

import arrow
import httpx
import tldextract
from dacite import from_dict
from fastapi import HTTPException
from loguru import logger
from shapely.geometry import Point

from flight_blender.auth.dss_auth import get_dss_auth_token
from flight_blender.auth.token_audience import generate_audience_from_base_url
from flight_blender.auth.token_cache import get_redis
from flight_blender.clients.dss_constraint_client import ConstraintOperations
from flight_blender.config import settings
from flight_blender.db.session import async_task_session
from flight_blender.domain_types.common import OPINT_INDEX_BASEPATH, OperationStateCode
from flight_blender.domain_types.scd import (
    HIGH_PRIORITY_OP_INTENT,
    OPINT_UPDATE_NOT_SUBMITTED_STATUS,
    SELF_NOTIFICATION_AUDIENCES,
    Altitude,
    Circle,
    ClearAreaResponse,
    ClearAreaResponseOutcome,
    CommonDSS2xxResponse,
    CommonDSS4xxResponse,
    CommonPeer9xxResponse,
    CompositeOperationalIntentPayload,
    DeleteOperationalIntentConstuctor,
    DeleteOperationalIntentResponse,
    DeleteOperationalIntentResponseSuccess,
    FlightDeclarationOperationalIntentStorageDetails,
    FlightPlanCurrentStatus,
    ImplicitSubscriptionParameters,
    LatLngPoint,
    NotifyPeerUSSPostPayload,
    OperationalIntentDetailsUSSResponse,
    OperationalIntentReference,
    OperationalIntentReferenceDSSResponse,
    OperationalIntentState,
    OperationalIntentStorage,
    OperationalIntentSubmissionStatus,
    OperationalIntentSubmissionSuccess,
    OperationalIntentTestInjection,
    OperationalIntentUpdateRequest,
    OperationalIntentUpdateResponse,
    OperationalIntentUpdateSuccessResponse,
    OperationalIntentUSSDetails,
    OpInttoCheckDetails,
    OpIntUpdateCheckResultCodes,
    OtherError,
    QueryOperationalIntentPayload,
    Radius,
    ShouldSendtoDSSProcessingResponse,
    SubmissionResultStatus,
    SubscriberToNotify,
    SubscriptionState,
    Time,
    USSNotificationResponse,
    Volume3D,
    Volume4D,
)
from flight_blender.domain_types.scd import Polygon as Plgn
from flight_blender.repositories.constraint_repo import SQLAlchemyConstraintRepository
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.utils import spatial_rid as rtree_helper
from flight_blender.utils.json_codecs import LazyEncoder
from flight_blender.utils.scd_helpers import PeerOperationalIntentValidator, VolumesConverter

HTTP_TIMEOUT_SECONDS = 30.0

# DSS operational-intent submission rejection codes → human-readable notes stored on the flight declaration.
_DSS_SUBMISSION_REJECTION_NOTES: dict[int, str] = {
    400: "Error during submission of operational intent, the DSS rejected because one or more parameters was missing",
    409: "Error during submission of operational intent, the DSS rejected it with because the latest airspace keys was not present",
    401: "There was a error in submitting the operational intent to the DSS, the token was invalid",
    403: "There was a error in submitting the operational intent to the DSS, the appropriate scope was not present",
    413: "There was a error in submitting the operational intent to the DSS, the operational intent was too large",
    429: "There was a error in submitting the operational intent to the DSS, too many requests were submitted to the DSS",
}
_DSS_SUBMISSION_REJECTION_CODES = (400, 409, 401, 403, 412, 413, 429)


def _json_from_response(response: httpx.Response, endpoint: str) -> dict:
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail={"message": f"Invalid JSON response from {endpoint}"}) from exc


def _http_exception_from_request_error(exc: httpx.RequestError, endpoint: str) -> HTTPException:
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(status_code=504, detail={"message": f"Request to {endpoint} timed out"})
    return HTTPException(status_code=502, detail={"message": f"Request to {endpoint} failed: {exc}"})


async def _async_request(method: str, endpoint: str, **kwargs: Any) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            return await client.request(method, endpoint, **kwargs)
    except httpx.RequestError as exc:
        raise _http_exception_from_request_error(exc, endpoint) from exc


class OperationalIntentReferenceHelper:
    """
    A class to parse Operational Intent References into Dataclass objects
    """

    async def parse_stored_operational_intent_details(self, operation_id: str) -> None | OperationalIntentStorage:
        """
        Parses and retrieves stored operational intent details for a given operation ID.
        This method interacts with the database to fetch operational intent details,
        including references, volumes, subscribers, and composite operational intent data.
        It processes the retrieved data into structured objects for further use.
        Args:
            operation_id (str): The unique identifier of the operation for which
                                the operational intent details are to be retrieved.
        Returns:
            Union[None, OperationalIntentStorage]:
                - An instance of `OperationalIntentStorage` containing the parsed operational intent details
                  if the operation ID exists in the database.
                - `None` if no operational intent reference is found for the given operation ID.
        Raises:
            This method does not explicitly raise exceptions but relies on the database reader
            and JSON parsing to handle errors internally.
        Notes:
            - The method fetches data from multiple database tables, including operational intent references,
              details, and subscribers.
            - The retrieved volumes and off-nominal volumes are parsed into `Volume4D` objects.
            - The method constructs a composite operational intent storage object containing bounds,
              time intervals, altitude limits, and success response details.
        """

        async with async_task_session() as db:
            fd_repo = SQLAlchemyFlightDeclarationRepository(db)
            flight_operational_intent_reference = await fd_repo.get_opint_reference_by_declaration_id(uuid.UUID(operation_id))

            if not flight_operational_intent_reference:
                logger.error("Flight operational intent reference not found in the database")
                return None

            flight_operational_intent_details = await fd_repo.get_opint_detail_by_declaration_id(uuid.UUID(operation_id))
            operational_intent_subscribers = await fd_repo.get_subscribers_of_opint_reference(ref_id=flight_operational_intent_reference.id)

        subscribers: list[SubscriberToNotify] = []
        for s in operational_intent_subscribers:
            subscription_states = [
                SubscriptionState(
                    subscription_id=cur_s["subscription_id"],
                    notification_index=cur_s["notification_index"],
                )
                for cur_s in json.loads(s.subscriptions)
            ]
            subscribers.append(SubscriberToNotify(subscriptions=subscription_states, uss_base_url=s.uss_base_url))

        volumes = json.loads(flight_operational_intent_details.volumes)
        off_nominal_volumes = json.loads(flight_operational_intent_details.off_nominal_volumes)
        priority = flight_operational_intent_details.priority
        state = flight_operational_intent_reference.state

        operational_intent_reference_dss_repsonse = OperationalIntentReferenceDSSResponse(
            id=flight_operational_intent_reference.id,
            manager=flight_operational_intent_reference.manager,
            uss_availability=flight_operational_intent_reference.uss_availability,
            version=flight_operational_intent_reference.version,
            state=flight_operational_intent_reference.state,
            ovn=flight_operational_intent_reference.ovn,
            time_start=Time(
                format="RFC3339",
                value=flight_operational_intent_reference.time_start,
            ),
            time_end=Time(
                format="RFC3339",
                value=flight_operational_intent_reference.time_end,
            ),
            uss_base_url=flight_operational_intent_reference.uss_base_url,
            subscription_id=flight_operational_intent_reference.subscription_id,
        )

        all_volumes: list[Volume4D] = []
        all_off_nominal_volumes: list[Volume4D] = []

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
        async with async_task_session() as db:
            fd_repo = SQLAlchemyFlightDeclarationRepository(db)
            composite_operational_intent_details = await fd_repo.get_composite_opint_by_declaration_id(uuid.UUID(operation_id))
        if composite_operational_intent_details is None:
            return None

        stored = OperationalIntentStorage(
            bounds=composite_operational_intent_details.bounds,
            start_datetime=composite_operational_intent_details.start_datetime,
            end_datetime=composite_operational_intent_details.end_datetime,
            alt_max=composite_operational_intent_details.alt_max,
            alt_min=composite_operational_intent_details.alt_min,
            success_response=OperationalIntentSubmissionSuccess(
                subscribers=subscribers,
                operational_intent_reference=operational_intent_reference_dss_repsonse,
            ),
            operational_intent_details=operational_intent_details,
        )
        return stored

    async def parse_and_load_stored_flight_operational_intent_reference(self, operation_id: str) -> OperationalIntentDetailsUSSResponse:
        """
        Given a stored flight operational intent, get the details of the operational intent
        """

        async with async_task_session() as db:
            fd_repo = SQLAlchemyFlightDeclarationRepository(db)
            flight_operational_intent_reference = await fd_repo.get_opint_reference_by_declaration_id(uuid.UUID(operation_id))

            if not flight_operational_intent_reference:
                logger.error("Flight operational intent reference not found in the database")
                return None
            flight_operational_intent_details = await fd_repo.get_opint_detail_by_declaration_id(uuid.UUID(operation_id))
        # Load existing opint details

        stored_operational_intent_id = flight_operational_intent_reference.id
        stored_manager = flight_operational_intent_reference.manager
        stored_uss_availability = flight_operational_intent_reference.uss_availability
        stored_version = flight_operational_intent_reference.version
        stored_state = flight_operational_intent_reference.state
        stored_ovn = flight_operational_intent_reference.ovn
        stored_uss_base_url = flight_operational_intent_reference.uss_base_url
        stored_subscription_id = flight_operational_intent_reference.subscription_id

        stored_time_start = Time(
            format="RFC3339",
            value=flight_operational_intent_reference.time_start,
        )
        stored_time_end = Time(
            format="RFC3339",
            value=flight_operational_intent_reference.time_end,
        )

        stored_priority = flight_operational_intent_details.priority
        stored_off_nominal_volumes = json.loads(flight_operational_intent_details.off_nominal_volumes)
        stored_volumes = json.loads(flight_operational_intent_details.volumes)

        details = self.parse_operational_intent_details(
            volumes=stored_volumes,
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

    def parse_operational_intent_details(self, volumes, priority: int, off_nominal_volumes=None) -> OperationalIntentUSSDetails:
        all_volumes: list[Volume4D] = []
        all_off_nominal_volumes: list[Volume4D] = []
        for volume in volumes:
            volume4D = self.parse_volume_to_volume4D(volume=volume)
            all_volumes.append(volume4D)
        if off_nominal_volumes:
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
        self.dss_base_url = settings.DSS_BASE_URL
        self.r = get_redis()
        self.constraints_helper = ConstraintOperations()

    async def get_nearby_operational_intents(self, volumes: list[Volume4D]) -> list[OperationalIntentDetailsUSSResponse]:
        # This method checks the USS network for any other volume in the airspace and queries the individual USS for data

        nearby_operational_intents = []
        # Get auth token with DSS audience (not self audience)
        dss_audience = generate_audience_from_base_url(self.dss_base_url)
        auth_token = await self.async_get_auth_token(audience=dss_audience)
        if not auth_token or "error" in auth_token:
            logger.error("Failed to get auth token for DSS query")
            return []
        # Query the DSS for operational intentns
        query_op_int_url = self.dss_base_url + "dss/v1/operational_intent_references/query"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        flight_blender_base_url = settings.FLIGHTBLENDER_FQDN
        my_op_int_ref_helper = OperationalIntentReferenceHelper()
        all_uss_operational_intent_details = []

        for volume in volumes:
            op_int_details_retrieved = False
            operational_intent_references = []
            area_of_interest = QueryOperationalIntentPayload(area_of_interest=volume)
            logger.info("Querying DSS for operational intents in the area..")
            logger.debug(f"Area of interest {json.dumps(asdict(area_of_interest))}")
            operational_intent_ref_response = await _async_request(
                "POST",
                query_op_int_url,
                json=json.loads(json.dumps(asdict(area_of_interest))),
                headers=headers,
            )
            # The DSS returned operational intent references as a list
            dss_operational_intent_references = _json_from_response(operational_intent_ref_response, query_op_int_url)
            logger.debug(f"DSS Response {dss_operational_intent_references}")
            if "operational_intent_references" in dss_operational_intent_references:
                operational_intent_references = dss_operational_intent_references["operational_intent_references"]
            else:
                logger.error("DSS query did not return operational_intent_references: %s" % dss_operational_intent_references)
                operational_intent_references = []

            # Query the operational intent reference details
            for operational_intent_reference_detail in operational_intent_references:
                # Get the USS URL endpoint
                dss_op_int_details_url = self.dss_base_url + "dss/v1/operational_intent_references/" + operational_intent_reference_detail["id"]
                # get new auth token for USS
                op_int_uss_details = await _async_request("GET", dss_op_int_details_url, headers=headers)
                operational_intent_reference = _json_from_response(op_int_uss_details, dss_op_int_details_url)
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
                logger.info("All Operational intents in the area..")

                # check the USS for flight volume by using the URL to see if this is stored in Flight Blender, DSS will return all intent details including our own
                current_uss_base_url = current_uss_operational_intent_detail.uss_base_url
                op_int_det = {}
                op_int_ref = {}
                if current_uss_base_url == flight_blender_base_url or current_uss_base_url.startswith(flight_blender_base_url + "/"):
                    # The opint is from Flight Blender itself
                    # No need to query peer USS, just update the ovn and process the volume locally

                    async with async_task_session() as db:
                        fd_repo = SQLAlchemyFlightDeclarationRepository(db)
                        flight_operational_intent_reference = await fd_repo.get_opint_reference_by_id(
                            uuid.UUID(str(current_uss_operational_intent_detail.id))
                        )
                        flight_operational_intent_reference_exists = flight_operational_intent_reference is not None

                        if flight_operational_intent_reference_exists:
                            flight_declaration_id = flight_operational_intent_reference.declaration_id
                            flight_operational_intent_detail = await fd_repo.get_opint_detail_by_declaration_id(flight_declaration_id)
                            await fd_repo.update_opint_reference_ovn(
                                ref_id=flight_operational_intent_reference.id,
                                ovn=current_uss_operational_intent_detail.ovn,
                            )
                            _op_int_ref = OperationalIntentReferenceDSSResponse(
                                subscription_id=current_uss_operational_intent_detail.subscription_id,
                                id=str(flight_operational_intent_reference.id),
                                uss_base_url=flight_operational_intent_reference.uss_base_url,
                                manager=flight_operational_intent_reference.manager,
                                uss_availability=flight_operational_intent_reference.uss_availability,
                                version=flight_operational_intent_reference.version,
                                state=flight_operational_intent_reference.state,
                                ovn=flight_operational_intent_reference.ovn,
                                time_start=Time(
                                    format="RFC3339",
                                    value=flight_operational_intent_reference.time_start,
                                ),
                                time_end=Time(
                                    format="RFC3339",
                                    value=flight_operational_intent_reference.time_end,
                                ),
                            )
                            op_int_ref = asdict(_op_int_ref)
                            op_int_det = {
                                "volumes": json.loads(flight_operational_intent_detail.volumes),
                                "off_nominal_volumes": json.loads(flight_operational_intent_detail.off_nominal_volumes),
                                "priority": flight_operational_intent_detail.priority,
                            }
                        else:
                            logger.warning(
                                "Flight operational intent reference not found in the database, this is a new operational intent with id: {uss_op_int_id}".format(
                                    uss_op_int_id=current_uss_operational_intent_detail.id
                                )
                            )
                            # Construct a minimal reference from the DSS data so the OVN
                            # is included in airspace keys (prevents DSS 409 errors)
                            _op_int_ref = OperationalIntentReferenceDSSResponse(
                                subscription_id=current_uss_operational_intent_detail.subscription_id,
                                id=current_uss_operational_intent_detail.id,
                                uss_base_url=current_uss_base_url,
                                manager=current_uss_operational_intent_detail.manager,
                                uss_availability=current_uss_operational_intent_detail.uss_availability,
                                version=current_uss_operational_intent_detail.version,
                                state=current_uss_operational_intent_detail.state,
                                ovn=current_uss_operational_intent_detail.ovn,
                                time_start=current_uss_operational_intent_detail.time_start,
                                time_end=current_uss_operational_intent_detail.time_end,
                            )
                            op_int_ref = asdict(_op_int_ref)
                            op_int_det = {"volumes": [], "off_nominal_volumes": [], "priority": 0}
                    op_int_details_retrieved = True

                else:  # This operational intent details is from a peer uss, need to query peer USS
                    uss_audience = generate_audience_from_base_url(base_url=current_uss_base_url)
                    uss_auth_token = await self.async_get_auth_token(audience=uss_audience)
                    logger.info(f"Auth Token {uss_auth_token}")

                    uss_headers = {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + uss_auth_token["access_token"],
                    }
                    # ASTM path is /uss/v1/operational_intents/{entityid}
                    uss_operational_intent_url = current_uss_base_url + "/uss/v1/operational_intents/" + current_uss_operational_intent_detail.id

                    logger.debug(f"Querying USS: {current_uss_base_url}")
                    uss_operational_intent_request = await _async_request("GET", uss_operational_intent_url, headers=uss_headers)

                    # Verify status of the response from the USS
                    if uss_operational_intent_request.status_code == 200:
                        # Request was successful
                        operational_intent_details_json = _json_from_response(uss_operational_intent_request, uss_operational_intent_url)
                        op_int_details_retrieved = True
                        # outline_polygon = None
                        # outline_circle = None

                        op_int_det = operational_intent_details_json["operational_intent"]["details"]
                        op_int_ref = operational_intent_details_json["operational_intent"]["reference"]
                    # The attempt to get data from the USS in the network failed
                    else:
                        logger.debug(f"Response: {_json_from_response(uss_operational_intent_request, uss_operational_intent_url)}")
                        logger.error(
                            "Error in querying peer USS about operational intent (ID: {uss_op_int_id}) details from uss with base url {uss_base_url}".format(
                                uss_op_int_id=current_uss_operational_intent_detail.id,
                                uss_base_url=current_uss_base_url,
                            )
                        )
                        raise HTTPException(
                            status_code=uss_operational_intent_request.status_code,
                            detail={"message": "Error in querying peer USS operational intent details"},
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
                    nearby_operational_intents.append(uss_op_int_details)

        return nearby_operational_intents

    def get_auth_token(self, audience: str = "") -> dict:
        """Fetch a DSS/peer-USS auth token synchronously.

        This is the patchable seam used by tests; the blocking network call is
        offloaded to a thread by :meth:`async_get_auth_token` on the async path.
        """
        return get_dss_auth_token(audience=audience, token_type="scd")  # nosec B106

    async def async_get_auth_token(self, audience: str = "") -> dict:
        """Async wrapper around :meth:`get_auth_token` that offloads the blocking call."""
        return await asyncio.to_thread(self.get_auth_token, audience=audience)

    async def delete_operational_intent(self, dss_operational_intent_ref_id: str, ovn: str) -> DeleteOperationalIntentResponse:
        """
        Deletes an operational intent from the DSS (Discovery and Synchronization Service).
        Args:
            dss_operational_intent_ref_id (str): The unique identifier of the operational intent to be deleted.
            ovn (str): The current version number (OVN) of the operational intent.
        Returns:
            DeleteOperationalIntentResponse: A response object containing the status, message, and any relevant data
            from the DSS regarding the deletion operation.
        Raises:
            HTTPError: If the HTTP request to the DSS fails or returns an unexpected status code.
        Notes:
            - The function sends a DELETE request to the DSS endpoint with the provided operational intent ID and OVN.
            - Handles various HTTP response codes (200, 404, 409, 412, etc.) and formats the response accordingly.
            - Requires a valid authentication token to interact with the DSS.
        """

        # Get auth token with DSS audience (not self audience)
        dss_audience = generate_audience_from_base_url(self.dss_base_url)
        auth_token = await self.async_get_auth_token(audience=dss_audience)
        if not auth_token or "error" in auth_token:
            logger.error("Failed to get auth token for DSS deletion")
            raise HTTPException(
                status_code=401,
                detail={"message": "Failed to get auth token for DSS deletion"},
            )

        dss_opint_delete_url = self.dss_base_url + "dss/v1/operational_intent_references/" + dss_operational_intent_ref_id + "/" + ovn

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }
        # Send the entity ID and OVN
        delete_payload = DeleteOperationalIntentConstuctor(entity_id=dss_operational_intent_ref_id, ovn=ovn)

        dss_r = await _async_request(
            "DELETE",
            dss_opint_delete_url,
            json=json.loads(json.dumps(asdict(delete_payload))),
            headers=headers,
        )

        dss_response = _json_from_response(dss_r, dss_opint_delete_url)
        dss_request_status_code = dss_r.status_code

        if dss_request_status_code == 200:
            common_200_response = CommonDSS2xxResponse(message="Successfully deleted operational intent id %s" % dss_operational_intent_ref_id)
            dss_response_formatted = DeleteOperationalIntentResponseSuccess(
                subscribers=dss_response["subscribers"],
                operational_intent_reference=dss_response["operational_intent_reference"],
            )
            return DeleteOperationalIntentResponse(
                dss_response=dss_response_formatted,
                status=200,
                message=common_200_response,
            )

        messages = {
            404: "URL endpoint not found",
            409: "The provided ovn does not match the current version of existing operational intent",
            412: "The client attempted to delete the operational intent while marked as Down in the DSS",
        }
        raise HTTPException(
            status_code=dss_request_status_code,
            detail={"message": messages.get(dss_request_status_code, "A error occurred while deleting the operational intent")},
        )

    async def get_and_process_nearby_operational_intents(self, volumes: list[Volume4D]) -> dict | bool:
        """This method processes the downloaded operational intents in to a GeoJSON object"""
        feat_collection = {"type": "FeatureCollection", "features": []}
        try:
            nearby_operational_intents = await self.get_nearby_operational_intents(volumes=volumes)
        except ConnectionError:
            raise ConnectionError("Could not reach peer USS for querying operational intent data")

        my_peer_uss_data_validator = PeerOperationalIntentValidator()
        all_received_intents_valid = my_peer_uss_data_validator.validate_nearby_operational_intents(
            nearby_operational_intents=nearby_operational_intents
        )
        logger.info(
            "Validation processing completed for all received operational intents, result: {validation_status}".format(
                validation_status=all_received_intents_valid
            )
        )
        if not all_received_intents_valid:
            raise ValueError("Error in validating received data, cannot progress with processing")

        for uss_op_int_detail in nearby_operational_intents:
            try:
                operational_intent_reference_id = uss_op_int_detail.reference.id
                operational_intent_reference_manager = uss_op_int_detail.reference.manager
                operational_intent_state = uss_op_int_detail.reference.state
            except AttributeError:
                operational_intent_reference_id = "unknown"
                operational_intent_reference_manager = "unknown"
            operational_intent_volumes = uss_op_int_detail.details.volumes
            my_volume_converter = VolumesConverter()
            my_volume_converter.convert_volumes_to_geojson(volumes=operational_intent_volumes)
            for f in my_volume_converter.geo_json["features"]:
                f["properties"]["operational_intent_reference_id"] = operational_intent_reference_id
                f["properties"]["operational_intent_reference_manager"] = operational_intent_reference_manager
                f["properties"]["operational_intent_state"] = operational_intent_state
                feat_collection["features"].append(f)

        return feat_collection

    async def get_latest_airspace_constraints_ovn(self, volumes: list[Volume4D]) -> list | list[str]:
        # Get the latest constraints from DSS

        all_nearby_constraints = await self.constraints_helper.get_nearby_constraints(volumes=volumes)
        # self.constraints_writer.write_nearby_constraints(constraints=all_nearby_constraints)
        latest_constraints_ovns: list[str] = []

        for constraint in all_nearby_constraints:
            if constraint.reference.ovn:
                latest_constraints_ovns.append(constraint.reference.ovn)

        return latest_constraints_ovns

    async def get_latest_airspace_volumes(self, volumes: list[Volume4D]) -> list | list[OpInttoCheckDetails]:
        # This method checks if a flight volume has conflicts with any other volume in the airspace
        all_opints_to_check = []
        try:
            nearby_operational_intents = await self.get_nearby_operational_intents(volumes=volumes)
        except ConnectionError:
            logger.info("Raising Connection Error 2")
            raise ConnectionError("Could not reach peer USS for querying operational intent data")

        my_peer_uss_data_validator = PeerOperationalIntentValidator()
        all_received_intents_valid = my_peer_uss_data_validator.validate_nearby_operational_intents(
            nearby_operational_intents=nearby_operational_intents
        )
        logger.info(
            "Validation processing completed for all received operational intents (SCD), result: {validation_status}".format(
                validation_status=all_received_intents_valid
            )
        )
        if not all_received_intents_valid:
            raise ValueError("Error in validating received data, cannot progress with processing")

        for uss_op_int_detail in nearby_operational_intents:
            if uss_op_int_detail.details.off_nominal_volumes:
                operational_intent_volumes = uss_op_int_detail.details.off_nominal_volumes
            else:
                operational_intent_volumes = uss_op_int_detail.details.volumes
            my_volume_converter = VolumesConverter()
            if operational_intent_volumes:
                my_volume_converter.convert_volumes_to_geojson(volumes=operational_intent_volumes)
                time_start = my_volume_converter.get_earliest_time_from_volumes()
                time_end = my_volume_converter.get_latest_time_from_volumes()
                minimum_rotated_rect = my_volume_converter.get_minimum_rotated_rectangle()
            else:
                # Empty volumes — use a tiny degenerate shape so the OVN is still
                # included in airspace keys (prevents DSS "OVNs not provided" 409)
                # but the shape won't intersect any real flight volumes
                minimum_rotated_rect = Point(0, 0).buffer(0.00001)
                time_start = uss_op_int_detail.reference.time_start.value
                time_end = uss_op_int_detail.reference.time_end.value
            cur_op_int_details = OpInttoCheckDetails(
                shape=minimum_rotated_rect,
                ovn=uss_op_int_detail.reference.ovn,
                id=uss_op_int_detail.reference.id,
                time_end=time_end,
                time_start=time_start,
            )
            all_opints_to_check.append(cur_op_int_details)

        return all_opints_to_check

    async def notify_peer_uss_of_created_updated_operational_intent(
        self,
        uss_base_url: str,
        notification_payload: NotifyPeerUSSPostPayload,
        audience: str,
    ):
        """This method posts operational intent details to peer USS via a POST request to /uss/v1/operational_intents"""
        auth_token = await self.async_get_auth_token(audience=audience)
        if not auth_token or "error" in auth_token:
            logger.error("Failed to get auth token for peer USS notification")
            raise HTTPException(
                status_code=401,
                detail={"message": "Failed to get auth token for peer USS notification"},
            )

        # ASTM path is /uss/v1/operational_intents (POST)
        notification_url = uss_base_url + "/uss/v1/operational_intents"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        uss_r = await _async_request(
            "POST",
            notification_url,
            json=json.loads(json.dumps(asdict(notification_payload))),
            headers=headers,
        )

        uss_r_status_code = uss_r.status_code

        if uss_r_status_code == 204:
            result_message = CommonDSS2xxResponse(message="Notified successfully")
            logger.info("Peer USS notified successfully")
        else:
            logger.info(
                "Error in notifying peer USS at {endpoint}, the request resulted in a {uss_r_status_code} response from the peer".format(
                    endpoint=notification_url, uss_r_status_code=uss_r_status_code
                )
            )
            raise HTTPException(
                status_code=uss_r_status_code,
                detail={"message": "Error in notification"},
            )

        notification_result = USSNotificationResponse(status=uss_r_status_code, message=result_message)

        return notification_result

    async def process_peer_uss_notifications(
        self,
        all_subscribers: list[SubscriberToNotify],
        operational_intent_details: OperationalIntentUSSDetails,
        operational_intent_reference: OperationalIntentReferenceDSSResponse,
        operational_intent_id: str,
    ):
        """This method sends a notification to all the subscribers of the operational intent reference in the DSS"""
        for subscriber in all_subscribers:
            domain_to_check = tldextract.extract(subscriber.uss_base_url)
            if domain_to_check.subdomain != "dummy" and domain_to_check.domain != "uss":
                operational_intent = OperationalIntentDetailsUSSResponse(
                    reference=operational_intent_reference,
                    details=operational_intent_details,
                )

                notification_payload = NotifyPeerUSSPostPayload(
                    operational_intent_id=operational_intent_id,
                    operational_intent=operational_intent,
                    subscriptions=subscriber.subscriptions,
                )
                audience = generate_audience_from_base_url(base_url=subscriber.uss_base_url)

                # Skip self-notifications (Flight Blender notifying itself)
                flight_blender_url = settings.FLIGHTBLENDER_FQDN
                if subscriber.uss_base_url == flight_blender_url or subscriber.uss_base_url.startswith(flight_blender_url + "/"):
                    continue
                if audience not in SELF_NOTIFICATION_AUDIENCES:
                    await self.notify_peer_uss_of_created_updated_operational_intent(
                        uss_base_url=subscriber.uss_base_url,
                        notification_payload=notification_payload,
                        audience=audience,
                    )

    def process_retrieved_airspace_volumes(
        self,
        current_network_opint_details_full: list[OpInttoCheckDetails],
        operational_intent_ref_id: str,
    ) -> list[OpInttoCheckDetails]:
        """The DSS returns all the volumes including ours, We dont need to check deconflicton for operation ID that we are updating, we therefore remove this from our deconfliction check and also update stored OVN"""

        operational_intent_details_to_check = list(
            filter(
                lambda op_int_to_check: op_int_to_check.id != operational_intent_ref_id,
                current_network_opint_details_full,
            )
        )
        return operational_intent_details_to_check

    def get_updated_ovn(
        self,
        current_network_opint_details_full: list[OpInttoCheckDetails],
        operational_intent_ref_id: str,
    ) -> None | str:
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

    def generate_airspace_keys(self, current_network_opint_details_full: list[OpInttoCheckDetails]) -> list[str]:
        airspace_keys = []
        for cur_op_int_detail in current_network_opint_details_full:
            airspace_keys.append(cur_op_int_detail.ovn)
        return airspace_keys

    def check_extents_conflict_with_latest_volumes(
        self,
        all_existing_operational_intent_details: list[OpInttoCheckDetails],
        extents: list[Volume4D],
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
        self,
        current_state: str,
        new_state: str,
        extents_conflict_with_dss_volumes: bool,
        priority: int,
    ) -> ShouldSendtoDSSProcessingResponse:
        should_opint_be_sent_to_dss = ShouldSendtoDSSProcessingResponse(
            should_submit_update_payload_to_dss=0,
            check_id=OpIntUpdateCheckResultCodes.Z,
            tentative_flight_plan_processing_response=FlightPlanCurrentStatus.Processing,
        )

        activated = OperationalIntentState.Activated.value
        off_nominal_states = (OperationalIntentState.Nonconforming.value, OperationalIntentState.Contingent.value)

        if current_state == activated and new_state == activated and extents_conflict_with_dss_volumes:
            logger.debug("Case B")
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = 0
            should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.B
            should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.OkToFly
        elif current_state == activated or new_state in off_nominal_states:
            # NOTE: this branch subsumes the former (unreachable) "Case C" for Activated→Activated.
            logger.debug("Case A")
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = 1
            should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.A
            should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.OffNominal
        elif priority == HIGH_PRIORITY_OP_INTENT:
            logger.debug("Case D")
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = 1
            should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.D
        else:
            submit_update_payload_to_dss = 0 if extents_conflict_with_dss_volumes else 1
            should_opint_be_sent_to_dss.should_submit_update_payload_to_dss = submit_update_payload_to_dss
            if submit_update_payload_to_dss:
                should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.E
                should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.Planned
            else:
                should_opint_be_sent_to_dss.check_id = OpIntUpdateCheckResultCodes.F
                should_opint_be_sent_to_dss.tentative_flight_plan_processing_response = FlightPlanCurrentStatus.NotPlanned

        logger.info("Update payload check complete..")

        return should_opint_be_sent_to_dss

    async def update_specified_operational_intent_reference(
        self,
        operational_intent_ref_id: str,
        extents: list[Volume4D],
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
            OperationalIntentUpdateResponse: The response of the update operation, or None if the update is not submitted.
        """
        # Get auth token with DSS audience (not self audience)
        dss_audience = generate_audience_from_base_url(self.dss_base_url)
        auth_token = await self.async_get_auth_token(audience=dss_audience)
        if not auth_token or "error" in auth_token:
            logger.error("Failed to get auth token for DSS update")
            raise HTTPException(
                status_code=401,
                detail={"message": "Failed to get auth token for DSS update"},
            )
        logger.info(f"Updating operational intent reference: {operational_intent_ref_id}")
        flight_blender_base_url = settings.FLIGHTBLENDER_FQDN

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
            current_network_opint_details_full = await self.get_latest_airspace_volumes(volumes=extents)
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail={"message": "Error in updating operational intent in the DSS, peer USS shared invalid data"},
            ) from exc
        except ConnectionError as exc:
            logger.info("Raising Connection Error 3")
            logger.info("Connection error with peer USS, cannot update volume...")
            raise HTTPException(
                status_code=504,
                detail={"message": "Error in updating operational intent in the DSS, peer USS unavailable"},
            ) from exc
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

        constraints_ovns = await self.get_latest_airspace_constraints_ovn(volumes=extents)
        if constraints_ovns:
            airspace_keys.extend(constraints_ovns)
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
            # Domain decision (not a transport error): Flight Blender will not submit this update to the
            # DSS. Return a rejection result so the flight-planning layer can answer HTTP 200 with the
            # appropriate planning_result, per the InterUSS flight-planning interface.
            logger.info("Update to flight will not be processed, not submitting to DSS")
            return OperationalIntentUpdateResponse(
                dss_response=CommonPeer9xxResponse(message="Update not submitted to DSS"),
                status=OPINT_UPDATE_NOT_SUBMITTED_STATUS,
                message="Update to flight will not be processed, will not be submitting to DSS",
                additional_information=pre_submission_checks,
            )

        dss_opint_update_url = self.dss_base_url + "dss/v1/operational_intent_references/" + operational_intent_ref_id + "/" + updated_ovn
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }

        flight_blender_base_url = settings.FLIGHTBLENDER_FQDN
        dss_r = await _async_request(
            "PUT",
            dss_opint_update_url,
            json=json.loads(json.dumps(asdict(operational_intent_update_payload), cls=LazyEncoder)),
            headers=headers,
        )
        dss_response = _json_from_response(dss_r, dss_opint_update_url)
        dss_request_status_code = dss_r.status_code
        if dss_r.status_code != 200:
            raise HTTPException(
                status_code=dss_request_status_code,
                detail={"message": dss_response.get("message", "Error in updating operational intent in the DSS")},
            )
        # Update request was successful, notify the subscribers
        subscribers = dss_response["subscribers"]
        all_subscribers: list[SubscriberToNotify] = []
        for subscriber in subscribers:
            subscriptions = subscriber["subscriptions"]
            uss_base_url = subscriber["uss_base_url"]
            if uss_base_url != flight_blender_base_url and not uss_base_url.startswith(flight_blender_base_url + "/"):
                all_subscription_states: list[SubscriptionState] = []
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
        opint_update_result = OperationalIntentUpdateResponse(dss_response=d_r, status=dss_request_status_code, message=message)
        return opint_update_result

    async def create_and_submit_operational_intent_reference(
        self,
        state: str,
        priority: int,
        volumes: list[Volume4D],
        off_nominal_volumes: list[Volume4D],
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
        # Get auth token with DSS audience (not self audience)
        dss_audience = generate_audience_from_base_url(self.dss_base_url)
        auth_token = await self.async_get_auth_token(audience=dss_audience)
        if not auth_token or "error" in auth_token:
            logger.error("Failed to get auth token for DSS operational intent creation")
            raise HTTPException(
                status_code=401,
                detail={"message": "Failed to get auth token for DSS"},
            )

        # A token from authority was received, we can now submit the operational intent
        logger.info("Creating new operational intent...")
        new_entity_id = str(uuid.uuid4())
        management_key = str(uuid.uuid4())
        new_operational_intent_ref_creation_url = self.dss_base_url + "dss/v1/operational_intent_references/" + new_entity_id
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + auth_token["access_token"],
        }
        airspace_keys = []
        flight_blender_base_url = settings.FLIGHTBLENDER_FQDN
        # The ASTM path for GetOperationalIntentDetails is /uss/v1/operational_intents/{entityid}.
        # The interUSS qualifier constructs: {uss_base_url}{path}, so uss_base_url must NOT include /uss.
        implicit_subscription_parameters = ImplicitSubscriptionParameters(uss_base_url=flight_blender_base_url, notify_for_constraints=True)
        operational_intent_reference = OperationalIntentReference(
            extents=volumes,
            key=airspace_keys,
            state=state,
            uss_base_url=flight_blender_base_url,
            new_subscription=implicit_subscription_parameters,
        )
        # Query other USSes for operational intent
        # Check if there are conflicts (or not)
        logger.info("Checking flight de-confliction status...")
        # Get all operational intents in the area
        s = []
        try:
            all_existing_operational_intent_details = await self.get_latest_airspace_volumes(volumes=volumes)
        except ValueError as exc:
            logger.info("Cannot create a new operational intent, get latest airspace volumes from DSS failed..")
            raise HTTPException(
                status_code=502,
                detail={"message": "Cannot create a new operational intent, get latest airspace volumes from DSS failed, peer querying failed"},
            ) from exc

        except ConnectionError as exc:
            logger.info("Raising Connection Error 4")
            logger.info("Error in processing peer USS data, cannot create a new operational intent..")
            raise HTTPException(
                status_code=504,
                detail={"message": "Error in processing peer USS data, cannot create a new operational intent"},
            ) from exc

        if isinstance(all_existing_operational_intent_details, list):
            logger.info(
                "Found {all_existing_operational_intent_details:02} operational intent references in the DSS".format(
                    all_existing_operational_intent_details=len(all_existing_operational_intent_details)
                )
            )
        else:
            logger.info("No operational intent references found in the DSS")

        # Get all the constraints from DSS
        all_nearby_constraints = await self.constraints_helper.get_nearby_constraints(volumes=volumes)
        all_constraint_ovns = []
        for cur_constraint in all_nearby_constraints:
            all_constraint_ovns.append(cur_constraint.reference.ovn)

        # TODO: Check intersection

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
            volume_time_start = my_ind_volumes_converter.get_earliest_time_from_volumes()
            volume_time_end = my_ind_volumes_converter.get_latest_time_from_volumes()

            for cur_op_int_detail in all_existing_operational_intent_details:
                airspace_keys.append(cur_op_int_detail.ovn)

            is_conflicted_in_time = False
            is_conflicted_in_space = False
            if priority == HIGH_PRIORITY_OP_INTENT:
                deconflicted = True
            else:
                airspace_keys.append(management_key)
                is_conflicted_in_space = rtree_helper.check_polygon_intersection(
                    op_int_details=all_existing_operational_intent_details,
                    polygon_to_check=ind_volumes_polygon,
                )
                # if the polygon is conflicted in space check if they are also conflicted in time
                if is_conflicted_in_space:
                    is_conflicted_in_time = rtree_helper.check_time_intersection(
                        op_int_details=all_existing_operational_intent_details,
                        volume_time_end=volume_time_end,
                        volume_time_start=volume_time_start,
                    )

                deconflicted = False if any([is_conflicted_in_space, is_conflicted_in_time]) else True
        else:
            deconflicted = True
            logger.info("No existing operational intent references in the DSS, deconfliction status: %s" % deconflicted)

        if not deconflicted:
            # Domain rejection (not a transport error): the flight conflicts with existing intents.
            # Return a conflict result so the flight-planning layer answers HTTP 200 with a Rejected
            # planning_result, per the InterUSS flight-planning interface.
            logger.info("Flight not deconflicted, there are other flights in the area..")
            return OperationalIntentSubmissionStatus(
                dss_response=OtherError(notes="Flight not deconflicted, there are other flights in the area"),
                status=SubmissionResultStatus.ConflictWithFlight.value,
                status_code=409,
                message="Flight not deconflicted, there are other flights in the area",
                operational_intent_id="",
            )

        airspace_keys.extend(all_constraint_ovns)
        operational_intent_reference.key = airspace_keys

        opint_creation_payload = json.loads(json.dumps(asdict(operational_intent_reference)))

        dss_request = await _async_request(
            "PUT",
            new_operational_intent_ref_creation_url,
            json=opint_creation_payload,
            headers=headers,
        )
        dss_response = _json_from_response(dss_request, new_operational_intent_ref_creation_url)
        dss_request_status_code = dss_request.status_code

        if dss_request_status_code == 201:
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
            logger.debug(f"Response details from the DSS {dss_response}")
            d_r = OperationalIntentSubmissionStatus(
                status=SubmissionResultStatus.Success.value,
                status_code=201,
                message="Successfully created operational intent in the DSS",
                dss_response=dss_creation_response,
                operational_intent_id=new_entity_id,
                constraints=all_nearby_constraints,
            )
        elif dss_request_status_code in [400, 401, 403, 409, 413, 429]:
            logger.error("DSS operational intent reference creation error %s" % dss_request.text)
            raise HTTPException(
                status_code=dss_request_status_code,
                detail={"message": dss_response.get("message", dss_request.text)},
            )

        else:
            logger.error("Error submitting operational intent to the DSS: %s" % dss_response)
            raise HTTPException(
                status_code=dss_request_status_code,
                detail={"message": dss_response.get("message", "Error submitting operational intent to the DSS")},
            )

        return d_r


# ── DSSAreaClearHandler (from scd/utils.py) ───────────────────────────────────


class DSSAreaClearHandler:
    def __init__(self, request_id):
        self.request_id = request_id

    async def clear_area_request(self, extent_raw):
        my_scd_dss_helper = SCDOperations()
        my_operational_intent_parser = OperationalIntentReferenceHelper()
        volume4D = my_operational_intent_parser.parse_volume_to_volume4D(volume=extent_raw)
        my_geo_json_converter = VolumesConverter()
        my_geo_json_converter.convert_volumes_to_geojson(volumes=[volume4D])
        view_rect_bounds = my_geo_json_converter.get_bounds()
        async with async_task_session() as db:
            fd_repo = SQLAlchemyFlightDeclarationRepository(db)
            my_rtree_helper = rtree_helper.OperationalIntentsIndexFactory(index_name=OPINT_INDEX_BASEPATH, fd_repo=fd_repo)
            await my_rtree_helper.generate_active_flights_operational_intents_index()
            op_ints_exist = await my_rtree_helper.check_op_ints_exist()
        all_existing_op_ints_in_area = []
        if op_ints_exist:
            all_existing_op_ints_in_area = my_rtree_helper.check_box_intersection(view_box=view_rect_bounds)

        all_deletion_requests_status = []
        if all_existing_op_ints_in_area:
            for existing_op_ints_in_area in all_existing_op_ints_in_area:
                if existing_op_ints_in_area:
                    deletion_success = False
                    operation_id = existing_op_ints_in_area["flight_id"]
                    async with async_task_session() as db:
                        fd_repo = SQLAlchemyFlightDeclarationRepository(db)
                        composite_operational_intent = await fd_repo.get_composite_opint_by_declaration_id(uuid.UUID(operation_id))
                    if composite_operational_intent:
                        ovn = composite_operational_intent.operational_intent_reference.ovn
                        opint_id = composite_operational_intent.id
                        ovn_opint = {"ovn_id": ovn, "opint_id": opint_id}
                        logger.info("Deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
                        deletion_request = await my_scd_dss_helper.delete_operational_intent(
                            dss_operational_intent_ref_id=str(opint_id),
                            ovn=str(ovn),
                        )
                        if deletion_request.status == 200:
                            logger.info("Success in deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
                            deletion_success = True
                            async with async_task_session() as db:
                                fd_repo = SQLAlchemyFlightDeclarationRepository(db)
                                await fd_repo.delete(uuid.UUID(operation_id))
                        else:
                            logger.info("Failed to delete operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
                            logger.error(deletion_request.dss_response)
                            deletion_success = False
                        all_deletion_requests_status.append(deletion_success)

            message = (
                "Some operational intents in the area failed to clear"
                if not all(all_deletion_requests_status)
                else "All operational intents in the area cleared successfully"
            )
            clear_area_status = ClearAreaResponseOutcome(
                success=all(all_deletion_requests_status),
                message=message,
                timestamp=arrow.now().isoformat(),
            )
        else:
            clear_area_status = ClearAreaResponseOutcome(
                success=True,
                message="All operational intents in the area cleared successfully",
                timestamp=arrow.now().isoformat(),
            )
        await my_rtree_helper.clear_rtree_index()
        return ClearAreaResponse(outcome=clear_area_status)


# ── DSSOperationalIntentsCreator (from scd/opint_helper.py) ──────────────────


class DSSOperationalIntentsCreator:
    """Helper to submit an operational intent to the DSS based on an operation ID."""

    def __init__(self, flight_declaration_id: str):
        # Inline import: flight_declarations_svc imports this module, so a top-level import would be circular.
        from flight_blender.services.flight_declarations_svc import OperationalIntentsConverter  # noqa: PLC0415

        self.flight_declaration_id = flight_declaration_id
        self.my_scd_dss_helper = SCDOperations()
        self.my_operational_intent_reference_helper = OperationalIntentsConverter()

    async def validate_flight_declaration_start_end_time(self) -> bool:

        async with async_task_session() as db:
            fd_repo = SQLAlchemyFlightDeclarationRepository(db)
            flight_declaration = await fd_repo.get_by_id(uuid.UUID(self.flight_declaration_id))
        if not flight_declaration:
            return False
        now = arrow.now()
        two_hours_from_now = now.shift(hours=2)
        op_start_time = arrow.get(flight_declaration.start_datetime)
        op_end_time = arrow.get(flight_declaration.end_datetime)
        start_time_ok = op_start_time <= two_hours_from_now and op_start_time >= now
        end_time_ok = op_end_time <= two_hours_from_now and op_end_time >= now
        return False not in [start_time_ok, end_time_ok]

    async def submit_flight_declaration_to_dss(self):
        fd_id = uuid.UUID(self.flight_declaration_id)

        async with async_task_session() as db:
            fd_repo = SQLAlchemyFlightDeclarationRepository(db)
            flight_declaration = await fd_repo.get_by_id(fd_id)
        if not flight_declaration:
            logger.error("Flight Declaration with ID %s not found in the database" % self.flight_declaration_id)
            raise HTTPException(
                status_code=404,
                detail={"message": "Flight Declaration with ID %s not found in the database" % self.flight_declaration_id},
            )

        current_state = flight_declaration.state
        operational_intent = json.loads(flight_declaration.operational_intent)
        operational_intent_data = from_dict(
            data_class=FlightDeclarationOperationalIntentStorageDetails,
            data=operational_intent,
        )

        auth_token = await self.my_scd_dss_helper.async_get_auth_token()

        if "error" in auth_token:
            logger.error("Error in retrieving auth_token, check if the auth server is running properly, error details displayed above")
            logger.error(auth_token["error"])
            raise HTTPException(
                status_code=500,
                detail={"message": "Error in getting a token from the Auth server"},
            )
        else:
            op_int_submission_result = await self.my_scd_dss_helper.create_and_submit_operational_intent_reference(
                state=operational_intent_data.state,
                volumes=operational_intent_data.volumes,
                off_nominal_volumes=operational_intent_data.off_nominal_volumes,
                priority=operational_intent_data.priority,
            )

            if op_int_submission_result.status_code == 201:
                logger.info("Successfully created operational intent in the DSS, updating database..")
                operational_intent_details_payload = OperationalIntentUSSDetails(
                    volumes=operational_intent_data.volumes,
                    off_nominal_volumes=operational_intent_data.off_nominal_volumes,
                    priority=operational_intent_data.priority,
                )
                async with async_task_session() as db:
                    fd_repo = SQLAlchemyFlightDeclarationRepository(db)
                    created_flight_operational_intent_reference = await fd_repo.create_opint_reference(
                        declaration_id=fd_id,
                        payload=op_int_submission_result.dss_response.operational_intent_reference,
                    )
                    created_flight_operational_intent_detail = await fd_repo.create_opint_detail(
                        declaration_id=fd_id,
                        payload=operational_intent_details_payload,
                    )
                    if created_flight_operational_intent_detail and created_flight_operational_intent_reference:
                        generated_composite_operational_intent_data = (
                            self.my_operational_intent_reference_helper.generate_bounds_altitude_time_for_volumes(
                                operational_intent_details_payload=operational_intent_details_payload,
                                flight_declaration_id=self.flight_declaration_id,
                            )
                        )
                        composite_operational_intent_data = CompositeOperationalIntentPayload(
                            bounds=generated_composite_operational_intent_data.bounds,
                            start_datetime=generated_composite_operational_intent_data.start_datetime,
                            end_datetime=generated_composite_operational_intent_data.end_datetime,
                            alt_max=generated_composite_operational_intent_data.alt_max,
                            alt_min=generated_composite_operational_intent_data.alt_min,
                            operational_intent_reference_id=str(created_flight_operational_intent_reference.id),
                            operational_intent_details_id=str(created_flight_operational_intent_detail.id),
                        )
                        await fd_repo.create_or_update_composite_opint(
                            declaration_id=fd_id,
                            payload=composite_operational_intent_data,
                        )
                    logger.info("Updating state from Processing to Accepted...")
                    await fd_repo.update(fd_id, state=OperationStateCode.Accepted)
                    await fd_repo.add_state_history_entry(
                        flight_declaration_id=fd_id,
                        original_state=current_state,
                        new_state=OperationStateCode.Accepted,
                        notes="Operational Intent successfully submitted to DSS and is Accepted",
                    )
                if op_int_submission_result.constraints:
                    # Inline import: scd_svc imports this module, so a top-level import would be circular.
                    from flight_blender.services.scd_svc import ConstraintsWriter  # noqa: PLC0415

                    async with async_task_session() as db:
                        constraint_repo = SQLAlchemyConstraintRepository(db)
                        my_constraints_writer = ConstraintsWriter(constraint_repo=constraint_repo)
                        await my_constraints_writer.write_nearby_constraints(
                            flight_declaration=flight_declaration,
                            constraints=op_int_submission_result.constraints,
                        )
            elif op_int_submission_result.status_code in _DSS_SUBMISSION_REJECTION_CODES:
                notes = _DSS_SUBMISSION_REJECTION_NOTES.get(op_int_submission_result.status_code, "Unknown error during submission")
                logger.info(
                    "There was a error in submitting the operational intent to the DSS, the DSS rejected our submission with a {status_code} response code".format(
                        status_code=op_int_submission_result.status_code
                    )
                )
                async with async_task_session() as db:
                    fd_repo = SQLAlchemyFlightDeclarationRepository(db)
                    await fd_repo.update(fd_id, state=OperationStateCode.Rejected)
                    await fd_repo.add_state_history_entry(
                        flight_declaration_id=fd_id,
                        original_state=current_state,
                        new_state=OperationStateCode.Rejected,
                        notes=notes,
                    )
            elif op_int_submission_result.status_code == 500 and op_int_submission_result.message == SubmissionResultStatus.ConflictWithFlight.value:
                logger.info("Flight is not deconflicted, updating state from Processing to Rejected ..")
                async with async_task_session() as db:
                    fd_repo = SQLAlchemyFlightDeclarationRepository(db)
                    await fd_repo.update(fd_id, state=OperationStateCode.Rejected)
                    await fd_repo.add_state_history_entry(
                        flight_declaration_id=fd_id,
                        original_state=current_state,
                        new_state=OperationStateCode.Rejected,
                        notes="Flight was not deconflicted correctly",
                    )

        return op_int_submission_result

    def notify_peer_uss(self, uss_base_url: str, notification_payload):
        my_scd_dss_helper = SCDOperations()
        try:
            ext = tldextract.extract(uss_base_url)
        except Exception:
            uss_audience = "localhost"
        else:
            if ext.domain in ["localhost", "internal"]:
                uss_audience = "localhost"
            else:
                uss_audience = ".".join([ext.subdomain, ext.domain, ext.suffix])

        if ext.subdomain != "dummy" and ext.domain != "uss":
            asyncio.run(
                my_scd_dss_helper.notify_peer_uss_of_created_updated_operational_intent(
                    uss_base_url=uss_base_url,
                    notification_payload=notification_payload,
                    audience=uss_audience,
                )
            )


# ── SCDTestHarnessHelper (from scd/scd_test_harness_helper.py) ───────────────


class SCDTestHarnessHelper:
    """Used in the SCD Test harness to include transformations."""

    def __init__(self, fd_repo: SQLAlchemyFlightDeclarationRepository):
        self.my_operational_intent_helper = OperationalIntentReferenceHelper()
        self.r = get_redis()
        self.my_volumes_converter = VolumesConverter()
        self.my_operational_intent_comparator = rtree_helper.OperationalIntentComparisonFactory()
        self.fd_repo = fd_repo

    async def check_if_same_flight_id_exists(self, operation_id: str) -> bool:
        flight_operational_intent_reference = await self.fd_repo.get_opint_reference_by_declaration_id(uuid.UUID(operation_id))
        return bool(flight_operational_intent_reference)
