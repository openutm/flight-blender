import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import timedelta
from typing import List
from uuid import UUID

import arrow
from django.http import JsonResponse
from django.shortcuts import redirect
from dotenv import find_dotenv, load_dotenv
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from auth_helper.common import get_redis
from auth_helper.utils import requires_scopes
from common.data_definitions import (
    ARGONSERVER_READ_SCOPE,
    ARGONSERVER_WRITE_SCOPE,
    FLIGHT_OPINT_KEY,
    OPERATION_STATES,
    OPERATION_STATES_LOOKUP,
)
from common.database_operations import (
    ArgonServerDatabaseReader,
    ArgonServerDatabaseWriter,
)
from rid_operations import rtree_helper
from scd_operations.data_definitions import FlightDeclarationCreationPayload

from . import dss_scd_helper
from .flight_planning_data_definitions import (
    FlightPlanningInjectionData,
    FlightPlanningStatusResponse,
    FlightPlanningTestStatus,
)
from .scd_data_definitions import (
    CapabilitiesResponse,
    ClearAreaResponse,
    ClearAreaResponseOutcome,
    DeleteFlightResponse,
    DeleteFlightStatusResponseEnum,
    FlightAuthorizationDataPayload,
    OperationalIntentState,
    OperationalIntentStorage,
    OperationalIntentStorageVolumes,
    OperationalIntentSubmissionStatus,
    OperationalIntentTestInjection,
    OperationalIntentUSSDetails,
    SCDTestInjectionDataPayload,
    SCDTestStatusResponse,
    SuccessfulOperationalIntentFlightIDStorage,
    USSCapabilitiesResponseEnum,
    Volume4D,
)
from .scd_test_harness_helper import (
    FlightPlanningDataProcessor,
    FlightPlantoOperationalIntentProcessor,
    SCDTestHarnessHelper,
    conflict_with_flight_test_injection_response,
    failed_planning_response,
    failed_test_injection_response,
    flight_planning_deletion_failure_response,
    flight_planning_deletion_success_response,
    not_planned_planning_response,
    planned_planning_response,
    planned_test_injection_response,
    ready_to_fly_injection_response,
    ready_to_fly_planning_response,
    rejected_test_injection_response,
)
from .utils import (
    DSSAreaClearHandler,
    OperatorRegistrationNumberValidator,
    UAVSerialNumberValidator,
)

load_dotenv(find_dotenv())
INDEX_NAME = "opint_proc"

logger = logging.getLogger("django")


def is_valid_uuid(uuid_to_test, version=4):
    try:
        uuid_obj = UUID(uuid_to_test, version=version)
    except ValueError:
        return False
    return str(uuid_obj) == uuid_to_test


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if is_dataclass(o):
            return asdict(o)
        return super().default(o)


@api_view(["GET"])
@requires_scopes(["utm.inject_test_data"])
def scd_test_status(request):
    status = SCDTestStatusResponse(status="Ready", version="latest")
    return JsonResponse(json.loads(json.dumps(status, cls=EnhancedJSONEncoder)), status=200)


@api_view(["GET"])
@requires_scopes(["utm.inject_test_data"])
def scd_test_capabilities(request):
    status = CapabilitiesResponse(
        capabilities=[
            USSCapabilitiesResponseEnum.BasicStrategicConflictDetection,
            USSCapabilitiesResponseEnum.FlightAuthorisationValidation,
            USSCapabilitiesResponseEnum.HighPriorityFlights,
        ]
    )
    return JsonResponse(json.loads(json.dumps(status, cls=EnhancedJSONEncoder)), status=200)


@api_view(["GET"])
@requires_scopes([ARGONSERVER_READ_SCOPE])
def scd_capabilities(request):
    return redirect(scd_test_capabilities)


@api_view(["POST"])
@requires_scopes(["utm.inject_test_data"])
def scd_clear_area_request(request):
    clear_area_request = request.data
    try:
        extent_raw = clear_area_request["extent"]
    except KeyError as ke:
        return Response(
            {"result": "Could not parse clear area payload, expected key %s not found " % ke},
            status=status.HTTP_400_BAD_REQUEST,
        )
    r = get_redis()
    # Convert the extent to V4D
    # my_redis_helper = RedisHelper()
    # my_redis_helper.delete_all_opints()

    # Create a list of Volume4D objects
    my_operational_intent_parser = dss_scd_helper.OperationalIntentReferenceHelper()
    volume4D = my_operational_intent_parser.parse_volume_to_volume4D(volume=extent_raw)
    my_geo_json_converter = dss_scd_helper.VolumesConverter()

    my_geo_json_converter.convert_volumes_to_geojson(volumes=[volume4D])
    view_rect_bounds = my_geo_json_converter.get_bounds()
    my_rtree_helper = rtree_helper.OperationalIntentsIndexFactory(index_name=INDEX_NAME)
    my_rtree_helper.generate_operational_intents_index(pattern="flight_opint.*")
    op_ints_exist = my_rtree_helper.check_op_ints_exist()
    all_existing_op_ints_in_area = []
    if op_ints_exist:
        all_existing_op_ints_in_area = my_rtree_helper.check_box_intersection(view_box=view_rect_bounds)

    all_deletion_requests_status = []
    if not all_existing_op_ints_in_area:
        clear_area_status = ClearAreaResponseOutcome(
            success=True,
            message="All operational intents in the area cleared successfully",
            timestamp=arrow.now().isoformat(),
        )
    for flight_details in all_existing_op_ints_in_area:
        if flight_details:
            deletion_success = False
            operation_id = flight_details["flight_id"]
            op_int_details_key = FLIGHT_OPINT_KEY + operation_id
            if r.exists(op_int_details_key):
                op_int_detail_raw = r.get(op_int_details_key)
                my_scd_dss_helper = dss_scd_helper.SCDOperations()
                # op_int_detail_raw = op_int_details.decode()
                op_int_detail = json.loads(op_int_detail_raw)
                ovn = op_int_detail["success_response"]["operational_intent_reference"]["ovn"]
                opint_id = op_int_detail["success_response"]["operational_intent_reference"]["id"]
                ovn_opint = {"ovn_id": ovn, "opint_id": opint_id}
                logger.info("Deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))

                deletion_request = my_scd_dss_helper.delete_operational_intent(dss_operational_intent_ref_id=opint_id, ovn=ovn)

                if deletion_request.status == 200:
                    logger.info("Success in deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
                    deletion_success = True
                all_deletion_requests_status.append(deletion_success)
    clear_area_status = ClearAreaResponseOutcome(
        success=all(all_deletion_requests_status),
        message="All operational intents in the area cleared successfully",
        timestamp=arrow.now().isoformat(),
    )

    my_rtree_helper.clear_rtree_index(pattern=FLIGHT_OPINT_KEY)
    clear_area_response = ClearAreaResponse(outcome=clear_area_status)
    return JsonResponse(json.loads(json.dumps(clear_area_response, cls=EnhancedJSONEncoder)), status=200)


@api_view(["PUT", "DELETE"])
@requires_scopes(["utm.inject_test_data"])
def scd_auth_test(request, operation_id):
    # This view implements the automated verification of SCD capabilities
    r = get_redis()
    my_operational_intent_parser = dss_scd_helper.OperationalIntentReferenceHelper()
    my_scd_dss_helper = dss_scd_helper.SCDOperations()
    my_geo_json_converter = dss_scd_helper.VolumesConverter()
    my_volumes_validator = dss_scd_helper.VolumesValidator()
    my_database_writer = ArgonServerDatabaseWriter()
    my_database_reader = ArgonServerDatabaseReader()

    operation_id_str = str(operation_id)
    logger.info("*********************")
    logger.info(operation_id_str)

    if request.method == "PUT":
        # Get the test data
        scd_test_data = request.data
        # Prase the flight authorization data set
        try:
            flight_authorization_data = scd_test_data["flight_authorisation"]
            f_a = FlightAuthorizationDataPayload(
                uas_serial_number=flight_authorization_data["uas_serial_number"],
                operation_category=flight_authorization_data["operation_category"],
                operation_mode=flight_authorization_data["operation_mode"],
                uas_class=flight_authorization_data["uas_class"],
                identification_technologies=flight_authorization_data["identification_technologies"],
                connectivity_methods=flight_authorization_data["connectivity_methods"],
                endurance_minutes=flight_authorization_data["endurance_minutes"],
                emergency_procedure_url=flight_authorization_data["emergency_procedure_url"],
                operator_id=flight_authorization_data["operator_id"],
            )
        except KeyError as ke:
            return Response(
                {"result": "Could not parse test injection payload, expected key %s not found " % ke},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Initial data for subscriptions
        opint_subscription_end_time = timedelta(seconds=180)
        # Parse the operational intent
        try:
            operational_intent = scd_test_data["operational_intent"]
            operational_intent_volumes = operational_intent["volumes"]
            operational_intent_off_nominal_volumes = operational_intent["off_nominal_volumes"]
        except KeyError as ke:
            return Response(
                {"result": "Could not parse test injection payload, expected key %s not found " % ke},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Create a list of Volume4D objects
        all_volumes: List[Volume4D] = []
        for volume in operational_intent_volumes:
            volume_4d = my_operational_intent_parser.parse_volume_to_volume4D(volume=volume)
            all_volumes.append(volume_4d)

        # Create a list of Volume4D objects
        all_off_nominal_volumes: List[Volume4D] = []
        for off_nominal_volume in operational_intent_off_nominal_volumes:
            off_nominal_volume_4d = my_operational_intent_parser.parse_volume_to_volume4D(volume=off_nominal_volume)
            all_off_nominal_volumes.append(off_nominal_volume_4d)

        # The state of the data coming in
        test_state = operational_intent["state"]
        # Parse the intent data and test state
        operational_intent_data = OperationalIntentTestInjection(
            volumes=all_volumes,
            priority=operational_intent["priority"],
            off_nominal_volumes=all_off_nominal_volumes,
            state=test_state,
        )
        # End parse the operational intent

        # Set the object for SCD Test Injection
        test_injection_data = SCDTestInjectionDataPayload(operational_intent=operational_intent_data, flight_authorisation=f_a)
        # Begin validation of operational intent
        my_operational_intent_validator = dss_scd_helper.OperationalIntentValidator(operational_intent_data=operational_intent_data)
        operational_intent_valid = my_operational_intent_validator.validate_operational_intent_test_data()
        if not operational_intent_valid:
            return Response(
                json.loads(json.dumps(rejected_test_injection_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )
        # End validation of operational intent
        # Begin validation of volumes
        volumes_valid = my_volumes_validator.validate_volumes(volumes=test_injection_data.operational_intent.volumes)

        if not volumes_valid:
            return Response(
                json.loads(json.dumps(rejected_test_injection_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )
        # End validation of Volumes

        # Begin validation of Flight Authorization
        my_serial_number_validator = UAVSerialNumberValidator(serial_number=test_injection_data.flight_authorisation.uas_serial_number)
        my_reg_number_validator = OperatorRegistrationNumberValidator(
            operator_registration_number=test_injection_data.flight_authorisation.operator_id
        )

        is_serial_number_valid = my_serial_number_validator.is_valid()
        is_reg_number_valid = my_reg_number_validator.is_valid()

        if not is_serial_number_valid:
            injection_response = asdict(rejected_test_injection_response)
            return Response(
                json.loads(json.dumps(injection_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )

        if not is_reg_number_valid:
            injection_response = asdict(rejected_test_injection_response)
            return Response(
                json.loads(json.dumps(injection_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )
        # End validation of Flight Authorization
        # Get auth token for DSS interactions
        auth_token = my_scd_dss_helper.get_auth_token()
        try:
            assert "error" not in auth_token
        except AssertionError as e:
            logger.error("Error in retrieving auth_token, check if the auth server is running properly, error details below")
            logger.error(e)
            logger.error(auth_token["error"])
            return Response(
                json.loads(json.dumps(failed_test_injection_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )
        # End get auth token for DSS interactions

        my_geo_json_converter.convert_volumes_to_geojson(volumes=all_volumes)
        view_rect_bounds = my_geo_json_converter.get_bounds()
        view_rect_bounds_storage = ",".join([str(i) for i in view_rect_bounds])
        view_r_bounds = ",".join(map(str, view_rect_bounds))

        # Check if operational intent exists in Argon Server
        my_test_harness_helper = SCDTestHarnessHelper()
        operational_intent_exists_in_argon_server = my_test_harness_helper.check_if_same_flight_id_exists(operation_id=operation_id_str)
        operational_intent_details_notification = OperationalIntentUSSDetails(
            volumes=test_injection_data.operational_intent.volumes,
            priority=test_injection_data.operational_intent.priority,
            off_nominal_volumes=test_injection_data.operational_intent.off_nominal_volumes,
        )
        if operational_intent_exists_in_argon_server and test_state in ["Activated", "Nonconforming"]:
            # Operational intent exists, update the operational intent based on SCD rules. Get the detail of the existing / stored operational intent
            existing_op_int_details = my_operational_intent_parser.parse_stored_operational_intent_details(operation_id=operation_id_str)
            flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id_str)
            if not flight_declaration:
                return Response(
                    {"result": "Flight Declaration with ID %s not found in Argon Server" % operation_id_str},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            flight_authorization = my_database_reader.get_flight_authorization_by_flight_declaration_obj(flight_declaration=flight_declaration)
            current_state = flight_declaration.state
            current_state_str = OPERATION_STATES[current_state][1]
            # ID of the operational intent reference stored in the DSS
            dss_operational_intent_id = flight_authorization.dss_operational_intent_id
            stored_operational_intent_details = my_operational_intent_parser.parse_and_load_stored_flight_opint(operation_id=operation_id_str)
            provided_volumes_off_nominal_volumes = test_injection_data.operational_intent.volumes
            deconfliction_check = True

            # If the flight is activated and submitted to the DSS and the new stat is non-conforming, submit off-nominal volumes and no need to check for de-confliction
            if current_state_str in ["Accepted", "Activated"] and test_state == "Nonconforming":
                provided_volumes_off_nominal_volumes = operational_intent_data.off_nominal_volumes
                deconfliction_check = False
            # If the flight state is activate and new state is also activated, check for deconfliction before updating the volumes
            elif current_state_str == "Activated" and test_state == "Activated":
                deconfliction_check = True

            operational_intent_update_job = my_scd_dss_helper.update_specified_operational_intent_reference(
                operational_intent_ref_id=stored_operational_intent_details.reference.id,
                extents=provided_volumes_off_nominal_volumes,
                new_state=test_state,
                current_state=current_state_str,
                ovn=stored_operational_intent_details.reference.ovn,
                subscription_id=stored_operational_intent_details.reference.subscription_id,
                deconfliction_check=deconfliction_check,
                priority=operational_intent_data.priority,
            )

            flight_opint_key = FLIGHT_OPINT_KEY + operation_id_str

            if operational_intent_update_job.status == 200:
                # The operational intent update in the DSS is successful, update storage
                # Update the redis storage for operational intent details so that when the USS endpoint is queried it will reflect the most updated state.

                # Notify the subscribers that the operational intent has been updated
                my_scd_dss_helper.process_peer_uss_notifications(
                    all_subscribers=operational_intent_update_job.dss_response.subscribers,
                    operational_intent_details=operational_intent_details_notification,
                    operational_intent_reference=operational_intent_update_job.dss_response.operational_intent_reference,
                    operational_intent_id=dss_operational_intent_id,
                )

                if test_state == "Activated":
                    # The current state is activated and the original state was also activated
                    ready_to_fly_injection_response.operational_intent_id = dss_operational_intent_id

                    # update the state to Activated
                    my_database_writer.update_flight_operation_state(flight_declaration_id=operation_id_str, state=2)

                    new_updated_operational_intent_full_details = OperationalIntentStorage(
                        bounds=view_r_bounds,
                        start_time=test_injection_data.operational_intent.volumes[0].time_start.value,
                        end_time=test_injection_data.operational_intent.volumes[0].time_end.value,
                        alt_max=test_injection_data.operational_intent.volumes[0].volume.altitude_upper.value,
                        alt_min=test_injection_data.operational_intent.volumes[0].volume.altitude_lower.value,
                        success_response=asdict(operational_intent_update_job.dss_response),
                        operational_intent_details=asdict(test_injection_data.operational_intent),
                    )

                elif test_state == "Nonconforming":
                    # Update the declaration to non-conforming
                    my_database_writer.update_flight_operation_state(flight_declaration_id=operation_id_str, state=3)
                    existing_op_int_details.operational_intent_details.off_nominal_volumes = all_off_nominal_volumes
                    existing_op_int_details.success_response.operational_intent_reference.state = OperationalIntentState.Nonconforming
                    existing_op_int_details.operational_intent_details.state = OperationalIntentState.Nonconforming
                    new_updated_operational_intent_full_details = existing_op_int_details
                    # Remove outline circle from off-nominal volumes

                r.set(
                    flight_opint_key,
                    json.dumps(asdict(new_updated_operational_intent_full_details)),
                )
                r.expire(name=flight_opint_key, time=opint_subscription_end_time)

                return Response(
                    json.loads(json.dumps(ready_to_fly_injection_response, cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )

            elif operational_intent_update_job.status == 999:
                # Deconfliction check failed during updating of operational intent
                logger.info("Flight not deconflicted...")
                return Response(
                    json.loads(
                        json.dumps(
                            conflict_with_flight_test_injection_response,
                            cls=EnhancedJSONEncoder,
                        )
                    ),
                    status=status.HTTP_200_OK,
                )
            else:
                # The update failed because the DSS returned a 4XX code
                logger.info("Updating of Operational intent failed...")
                return Response(
                    json.loads(json.dumps(failed_test_injection_response, cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )
        else:
            op_int_submission: OperationalIntentSubmissionStatus = my_scd_dss_helper.create_and_submit_operational_intent_reference(
                state=test_injection_data.operational_intent.state,
                volumes=test_injection_data.operational_intent.volumes,
                off_nominal_volumes=test_injection_data.operational_intent.off_nominal_volumes,
                priority=test_injection_data.operational_intent.priority,
            )

            if op_int_submission.status == "success":
                # Successfully submitted to the DSS, save the operational intent in Redis
                # Notify the subscribers that the operational intent has been updated
                my_scd_dss_helper.process_peer_uss_notifications(
                    all_subscribers=op_int_submission.dss_response.subscribers,
                    operational_intent_details=operational_intent_details_notification,
                    operational_intent_reference=op_int_submission.dss_response.operational_intent_reference,
                    operational_intent_id=op_int_submission.operational_intent_id,
                )

                operational_intent_full_details = OperationalIntentStorage(
                    bounds=view_r_bounds,
                    start_time=test_injection_data.operational_intent.volumes[0].time_start.value,
                    end_time=test_injection_data.operational_intent.volumes[0].time_end.value,
                    alt_max=50,
                    alt_min=25,
                    success_response=op_int_submission.dss_response,
                    operational_intent_details=test_injection_data.operational_intent,
                )
                # Store flight DSS response and operational intent reference
                flight_opint = FLIGHT_OPINT_KEY + operation_id_str
                logger.info("Flight with operational intent id {flight_opint} created".format(flight_opint=operation_id_str))
                r.set(flight_opint, json.dumps(asdict(operational_intent_full_details)))
                r.expire(name=flight_opint, time=opint_subscription_end_time)

                # Store the details of the operational intent reference
                flight_op_int_storage = SuccessfulOperationalIntentFlightIDStorage(
                    operation_id=operation_id_str,
                    operational_intent_id=op_int_submission.operational_intent_id,
                )
                opint_flightref = "opint_flightref." + op_int_submission.operational_intent_id

                r.set(opint_flightref, json.dumps(asdict(flight_op_int_storage)))
                r.expire(name=opint_flightref, time=opint_subscription_end_time)
                # End store flight DSS

                planned_test_injection_response.operational_intent_id = op_int_submission.operational_intent_id

                # Create a flight declaration with operation id
                volumes_to_store = OperationalIntentStorageVolumes(volumes=operational_intent_data.volumes)

                flight_declaration_creation_payload = FlightDeclarationCreationPayload(
                    id=operation_id_str,
                    operational_intent=json.dumps(asdict(volumes_to_store)),
                    flight_declaration_raw_geojson=json.dumps(my_geo_json_converter.geo_json),
                    bounds=view_rect_bounds_storage,
                    state=OPERATION_STATES_LOOKUP[test_state],
                    aircraft_id="0000",
                )

                my_database_writer.create_flight_declaration(flight_declaration_creation=flight_declaration_creation_payload)
                flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id_str)
                flight_authorization = my_database_writer.create_flight_authorization_with_submitted_operational_intent(
                    flight_declaration=flight_declaration,
                    dss_operational_intent_id=op_int_submission.operational_intent_id,
                )
                # End create flight dectiarion

            elif op_int_submission.status == "conflict_with_flight":
                # If conflict with flight is generated then no need to save response
                conflict_with_flight_test_injection_response.operational_intent_id = op_int_submission.operational_intent_id
                return Response(
                    json.loads(
                        json.dumps(
                            conflict_with_flight_test_injection_response,
                            cls=EnhancedJSONEncoder,
                        )
                    ),
                    status=status.HTTP_200_OK,
                )

            elif op_int_submission.status == "failure":
                # The flight was rejected by the DSS we will make it a failure and report back
                rejected_test_injection_response.operational_intent_id = op_int_submission.operational_intent_id
                return Response(
                    json.loads(
                        json.dumps(
                            rejected_test_injection_response,
                            cls=EnhancedJSONEncoder,
                        )
                    ),
                    status=status.HTTP_200_OK,
                )

            else:
                failed_test_injection_response.operational_intent_id = op_int_submission.operational_intent_id

                return Response(
                    json.loads(json.dumps(failed_test_injection_response, cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )

            if test_injection_data.operational_intent.state == "Activated":
                return Response(
                    json.loads(json.dumps(ready_to_fly_injection_response, cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    json.loads(
                        json.dumps(
                            asdict(planned_test_injection_response),
                            cls=EnhancedJSONEncoder,
                        )
                    ),
                    status=status.HTTP_200_OK,
                )

    elif request.method == "DELETE":
        op_int_details_key = FLIGHT_OPINT_KEY + operation_id_str
        op_int_detail_raw = r.get(op_int_details_key)

        if op_int_detail_raw:
            op_int_detail = json.loads(op_int_detail_raw)

            ovn = op_int_detail["success_response"]["operational_intent_reference"]["ovn"]
            opint_id = op_int_detail["success_response"]["operational_intent_reference"]["id"]
            ovn_opint = {"ovn_id": ovn, "opint_id": opint_id}
            logger.info("Deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
            my_scd_dss_helper.delete_operational_intent(dss_operational_intent_ref_id=opint_id, ovn=ovn)
            r.delete(op_int_details_key)
            my_database_writer.delete_flight_declaration(flight_declaration_id=operation_id_str)

            delete_flight_response = DeleteFlightResponse(
                result=DeleteFlightStatusResponseEnum.Closed,
                notes="The flight was closed successfully by the USS and is now out of the UTM system.",
            )
        else:
            delete_flight_response = DeleteFlightR
            esponse(
                result=DeleteFlightStatusResponseEnum.Failed,
                notes="The flight was not found in the USS, please check your flight ID %s" % operation_id_str,
            )

        return Response(
            json.loads(json.dumps(delete_flight_response, cls=EnhancedJSONEncoder)),
            status=status.HTTP_200_OK,
        )


# Flight Planning Close Flight Plan
@api_view(["PUT", "DELETE"])
@requires_scopes(["interuss.flight_planning.plan"])
def upsert_close_flight_plan(request, flight_plan_id):
    # Parse the incoming flight planning data

    r = get_redis()
    # flight_details_storage = "flight_details:" + str(flight_plan_id)
    my_operational_intent_parser = dss_scd_helper.OperationalIntentReferenceHelper()
    my_scd_dss_helper = dss_scd_helper.SCDOperations()
    my_geo_json_converter = dss_scd_helper.VolumesConverter()
    my_volumes_validator = dss_scd_helper.VolumesValidator()
    my_database_writer = ArgonServerDatabaseWriter()
    my_database_reader = ArgonServerDatabaseReader()

    operation_id_str = str(flight_plan_id)
    logger.info("*********************")
    logger.info(operation_id_str)

    if request.method == "PUT":
        scd_test_data = request.data
        my_flight_plan_processor = FlightPlanningDataProcessor(incoming_flight_information=scd_test_data)

        scd_test_data = my_flight_plan_processor.process_incoming_flight_plan_data()
        my_flight_plan_op_intent_bridge = FlightPlantoOperationalIntentProcessor(flight_planning_request=scd_test_data)

        opint_subscription_end_time = timedelta(seconds=180)
        flight_planning_off_nominal_volumes = []
        flight_planning_volumes = scd_test_data.intended_flight.basic_information.area

        flight_planning_priority = scd_test_data.intended_flight.astm_f3548_21.priority if scd_test_data.intended_flight.astm_f3548_21.priority else 0
        flight_planning_uas_state = scd_test_data.intended_flight.basic_information.uas_state.value
        flight_planning_usage_state = scd_test_data.intended_flight.basic_information.usage_state.value
        # Parse the intent data and test state
        flight_planning_data = FlightPlanningInjectionData(
            volumes=flight_planning_volumes,
            priority=flight_planning_priority,
            off_nominal_volumes=flight_planning_off_nominal_volumes,
            uas_state=flight_planning_uas_state,
            usage_state=flight_planning_usage_state,
            state="Accepted",
        )

        my_flight_planning_data_validator = dss_scd_helper.FlightPlanningDataValidator(incoming_flight_planning_data=flight_planning_data)
        flight_planning_data_valid = my_flight_planning_data_validator.validate_flight_planning_test_data()

        if not flight_planning_data_valid:
            logger.info("Flight Planning data not valid..")
            return Response(
                json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )

        volumes_valid = my_volumes_validator.validate_volumes(volumes=scd_test_data.intended_flight.basic_information.area)

        if not volumes_valid:
            return Response(
                json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )
        # End validation of Volumes

        # Begin validation of Flight Authorization
        my_serial_number_validator = UAVSerialNumberValidator(
            serial_number=scd_test_data.intended_flight.uspace_flight_authorisation.uas_serial_number
        )
        my_reg_number_validator = OperatorRegistrationNumberValidator(
            operator_registration_number=scd_test_data.intended_flight.uspace_flight_authorisation.operator_id
        )

        is_serial_number_valid = my_serial_number_validator.is_valid()
        is_reg_number_valid = my_reg_number_validator.is_valid()

        if not is_serial_number_valid:
            injection_response = asdict(not_planned_planning_response)
            return Response(
                json.loads(json.dumps(injection_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )

        if not is_reg_number_valid:
            injection_response = asdict(not_planned_planning_response)
            return Response(
                json.loads(json.dumps(injection_response, cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )

        auth_token = my_scd_dss_helper.get_auth_token()
        try:
            assert "error" not in auth_token
        except AssertionError as e:
            logger.error("Error in retrieving auth_token, check if the auth server is running properly, error details below")
            logger.error(e)
            logger.error(auth_token["error"])
            return Response(
                json.loads(json.dumps(asdict(failed_planning_response), cls=EnhancedJSONEncoder)),
                status=status.HTTP_200_OK,
            )
        # End get auth token for DSS interactions

        my_geo_json_converter.convert_volumes_to_geojson(volumes=flight_planning_volumes)
        view_rect_bounds = my_geo_json_converter.get_bounds()
        view_rect_bounds_storage = ",".join([str(i) for i in view_rect_bounds])
        view_r_bounds = ",".join(map(str, view_rect_bounds))

        # Check if operational intent exists in Argon Server
        my_test_harness_helper = SCDTestHarnessHelper()
        flight_plan_exists_in_argon_server = my_test_harness_helper.check_if_same_flight_id_exists(operation_id=operation_id_str)
        # Create a payload for notification
        flight_planning_notification_payload = flight_planning_data
        generated_operational_intent_state = my_flight_plan_op_intent_bridge.generate_operational_intent_state_from_planning_information()
        # Flight plan exists in Argon Server and the new state is off nominal or contingent
        if flight_plan_exists_in_argon_server and generated_operational_intent_state in ["Activated", "Nonconforming"]:
            # Operational intent exists, update the operational intent based on SCD rules. Get the detail of the existing / stored operational intent
            existing_op_int_details = my_operational_intent_parser.parse_stored_operational_intent_details(operation_id=operation_id_str)
            flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id_str)
            if not flight_declaration:
                failed_planning_response.notes = "Flight Declaration with ID %s not found in Argon Server" % operation_id_str

                return Response(
                    json.loads(json.dumps(asdict(failed_planning_response), cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )

            flight_authorization = my_database_reader.get_flight_authorization_by_flight_declaration_obj(flight_declaration=flight_declaration)
            current_state = flight_declaration.state
            current_state_str = OPERATION_STATES[current_state][1]
            # ID of the operational intent reference stored in the DSS
            dss_operational_intent_id = flight_authorization.dss_operational_intent_id
            stored_operational_intent_details = my_operational_intent_parser.parse_and_load_stored_flight_opint(operation_id=operation_id_str)
            provided_volumes_off_nominal_volumes = scd_test_data.intended_flight.basic_information.area
            deconfliction_check = True

            # If the flight is activated and submitted to the DSS and the new stat is non-conforming, submit off-nominal volumes and no need to check for de-confliction
            if current_state_str in ["Accepted", "Activated"] and generated_operational_intent_state == "Nonconforming":
                deconfliction_check = False
            # If the flight state is activate and new state is also activated, check for deconfliction before updating the volumes
            elif current_state_str == "Activated" and generated_operational_intent_state == "Activated":
                deconfliction_check = True

            operational_intent_update_job = my_scd_dss_helper.update_specified_operational_intent_reference(
                operational_intent_ref_id=stored_operational_intent_details.reference.id,
                extents=provided_volumes_off_nominal_volumes,
                new_state=generated_operational_intent_state,
                current_state=current_state_str,
                ovn=stored_operational_intent_details.reference.ovn,
                subscription_id=stored_operational_intent_details.reference.subscription_id,
                deconfliction_check=deconfliction_check,
                priority=scd_test_data.intended_flight.astm_f3548_21.priority,
            )

            flight_opint_key = FLIGHT_OPINT_KEY + operation_id_str

            if operational_intent_update_job.status == 200:
                # The operational intent update in the DSS is successful, update storage
                # Update the redis storage for operational intent details so that when the USS endpoint is queried it will reflect the most updated state.

                # Notify the subscribers that the operational intent has been updated

                my_scd_dss_helper.process_peer_uss_notifications(
                    all_subscribers=operational_intent_update_job.dss_response.subscribers,
                    operational_intent_details=flight_planning_notification_payload,
                    operational_intent_reference=operational_intent_update_job.dss_response.operational_intent_reference,
                    operational_intent_id=dss_operational_intent_id,
                )

                if generated_operational_intent_state == "Activated":
                    # The current state is activated and the original state was also activated
                    ready_to_fly_planning_response.notes = "Created Operational Intent ID {operational_intent_id}".format(
                        operational_intent_id=dss_operational_intent_id
                    )

                    # update the state to Activated
                    my_database_writer.update_flight_operation_state(flight_declaration_id=operation_id_str, state=2)

                    new_updated_operational_intent_full_details = OperationalIntentStorage(
                        bounds=view_r_bounds,
                        start_time=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                        end_time=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                        alt_max=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_upper.value,
                        alt_min=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_lower.value,
                        success_response=asdict(operational_intent_update_job.dss_response),
                        operational_intent_details=asdict(flight_planning_notification_payload),
                    )

                elif generated_operational_intent_state == "Nonconforming":
                    # Update the declaration to non-conforming
                    my_database_writer.update_flight_operation_state(flight_declaration_id=operation_id_str, state=3)
                    existing_op_int_details.operational_intent_details.off_nominal_volumes = scd_test_data.intended_flight.basic_information.area
                    existing_op_int_details.success_response.operational_intent_reference.state = OperationalIntentState.Nonconforming
                    existing_op_int_details.operational_intent_details.state = OperationalIntentState.Nonconforming
                    new_updated_operational_intent_full_details = existing_op_int_details
                    # Remove outline circle from off-nominal volumes

                r.set(
                    flight_opint_key,
                    json.dumps(asdict(new_updated_operational_intent_full_details)),
                )
                r.expire(name=flight_opint_key, time=opint_subscription_end_time)

                return Response(
                    json.loads(json.dumps(ready_to_fly_planning_response, cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )
            elif operational_intent_update_job.status == 999:
                # Deconfliction check failed during updating of operational intent
                logger.info("Flight not deconflicted...")
                return Response(
                    json.loads(
                        json.dumps(
                            not_planned_planning_response,
                            cls=EnhancedJSONEncoder,
                        )
                    ),
                    status=status.HTTP_200_OK,
                )
            else:
                # The update failed because the DSS returned a 4XX code
                logger.info("Updating of Operational intent failed...")
                return Response(
                    json.loads(json.dumps(failed_planning_response, cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )
        else:
            pre_creation_checks_passed = my_volumes_validator.pre_operational_intent_creation_checks(
                volumes=scd_test_data.intended_flight.basic_information.area
            )
            if not pre_creation_checks_passed:
                return Response(
                    json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )
            off_nominal_volumes = (
                scd_test_data.intended_flight.basic_information.area if flight_planning_uas_state in ["OffNominal", "Contingent"] else []
            )

            flight_planning_submission: OperationalIntentSubmissionStatus = my_scd_dss_helper.create_and_submit_operational_intent_reference(
                state=generated_operational_intent_state,
                volumes=scd_test_data.intended_flight.basic_information.area,
                off_nominal_volumes=off_nominal_volumes,
                priority=flight_planning_priority,
            )

            if flight_planning_submission.status == "success":
                # Successfully submitted to the DSS, save the operational intent in Redis
                # Notify the subscribers that the operational intent has been updated
                flight_planning_data.state = generated_operational_intent_state
                my_scd_dss_helper.process_peer_uss_notifications(
                    all_subscribers=flight_planning_submission.dss_response.subscribers,
                    operational_intent_details=flight_planning_notification_payload,
                    operational_intent_reference=flight_planning_submission.dss_response.operational_intent_reference,
                    operational_intent_id=flight_planning_submission.operational_intent_id,
                )

                operational_intent_full_details = OperationalIntentStorage(
                    bounds=view_r_bounds,
                    start_time=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                    end_time=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                    alt_max=50,
                    alt_min=25,
                    success_response=flight_planning_submission.dss_response,
                    operational_intent_details=flight_planning_data,
                )
                # Store flight DSS response and operational intent reference
                flight_opint = FLIGHT_OPINT_KEY + operation_id_str
                logger.info("Flight with operational intent id {flight_opint} created".format(flight_opint=operation_id_str))
                r.set(flight_opint, json.dumps(asdict(operational_intent_full_details)))
                r.expire(name=flight_opint, time=opint_subscription_end_time)

                # Store the details of the operational intent reference
                flight_op_int_storage = SuccessfulOperationalIntentFlightIDStorage(
                    operation_id=operation_id_str,
                    operational_intent_id=flight_planning_submission.operational_intent_id,
                )
                opint_flightref = "opint_flightref." + flight_planning_submission.operational_intent_id

                r.set(opint_flightref, json.dumps(asdict(flight_op_int_storage)))
                r.expire(name=opint_flightref, time=opint_subscription_end_time)
                # End store flight DSS
                planned_test_injection_response.operational_intent_id = flight_planning_submission.operational_intent_id
                # Create a flight declaration with operation id
                volumes_to_store = OperationalIntentStorageVolumes(volumes=scd_test_data.intended_flight.basic_information.area)

                flight_declaration_creation_payload = FlightDeclarationCreationPayload(
                    id=operation_id_str,
                    operational_intent=json.dumps(asdict(volumes_to_store)),
                    flight_declaration_raw_geojson=json.dumps(my_geo_json_converter.geo_json),
                    bounds=view_rect_bounds_storage,
                    state=OPERATION_STATES_LOOKUP[generated_operational_intent_state],
                    aircraft_id="0000",
                )

                my_database_writer.create_flight_declaration(flight_declaration_creation=flight_declaration_creation_payload)
                flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id_str)
                flight_authorization = my_database_writer.create_flight_authorization_with_submitted_operational_intent(
                    flight_declaration=flight_declaration,
                    dss_operational_intent_id=flight_planning_submission.operational_intent_id,
                )
                # End create operational intent

            elif flight_planning_submission.status == "conflict_with_flight":
                return Response(
                    json.loads(json.dumps(asdict(not_planned_planning_response), cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )

            elif flight_planning_submission.status in ["failure", "peer_uss_data_sharing_issue"]:
                logger.info(flight_planning_submission.status_code)
                if flight_planning_submission.status_code == 408:
                    return Response(
                        json.loads(json.dumps(asdict(not_planned_planning_response), cls=EnhancedJSONEncoder)),
                        status=status.HTTP_200_OK,
                    )

                else:
                    return Response(
                        json.loads(json.dumps(asdict(failed_planning_response), cls=EnhancedJSONEncoder)),
                        status=status.HTTP_200_OK,
                    )

            if scd_test_data.intended_flight.basic_information.usage_state == " Planned":
                return Response(
                    json.loads(json.dumps(asdict(ready_to_fly_planning_response), cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    json.loads(
                        json.dumps(
                            asdict(planned_planning_response),
                            cls=EnhancedJSONEncoder,
                        )
                    ),
                    status=status.HTTP_200_OK,
                )

    elif request.method == "DELETE":
        op_int_details_key = FLIGHT_OPINT_KEY + operation_id_str
        op_int_detail_raw = r.get(op_int_details_key)

        if op_int_detail_raw:
            op_int_detail = json.loads(op_int_detail_raw)

            ovn = op_int_detail["success_response"]["operational_intent_reference"]["ovn"]
            opint_id = op_int_detail["success_response"]["operational_intent_reference"]["id"]
            ovn_opint = {"ovn_id": ovn, "opint_id": opint_id}
            logger.info("Deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
            my_scd_dss_helper.delete_operational_intent(dss_operational_intent_ref_id=opint_id, ovn=ovn)
            r.delete(op_int_details_key)
            my_database_writer.delete_flight_declaration(flight_declaration_id=operation_id_str)

            flight_planning_deletion_response = flight_planning_deletion_success_response
        else:
            flight_planning_deletion_response = flight_planning_deletion_failure_response

        return Response(
            json.loads(json.dumps(flight_planning_deletion_response, cls=EnhancedJSONEncoder)),
            status=status.HTTP_200_OK,
        )


@api_view(["GET"])
@requires_scopes(["interuss.flight_planning.direct_automated_test"])
def flight_planning_status(request):
    status = FlightPlanningTestStatus(
        status=FlightPlanningStatusResponse.Ready,
        system_version="v0.1",
        api_name="Flight Planning Automated Testing Interface",
        api_version="latest",
    )
    return JsonResponse(json.loads(json.dumps(status, cls=EnhancedJSONEncoder)), status=200)


@api_view(["POST"])
@requires_scopes(["interuss.flight_planning.direct_automated_test"])
def flight_planning_clear_area_request(request):
    clear_area_request = request.data
    try:
        request_id = clear_area_request["request_id"]
        extent_raw = clear_area_request["extent"]
    except KeyError as ke:
        return Response(
            {"result": "Could not parse clear area payload, expected key %s not found " % ke},
            status=status.HTTP_400_BAD_REQUEST,
        )
    my_flight_plan_clear_area_handler = DSSAreaClearHandler(request_id=request_id)
    clear_area_response = my_flight_plan_clear_area_handler.clear_area_request(extent_raw=extent_raw)
    return JsonResponse(json.loads(json.dumps(clear_area_response, cls=EnhancedJSONEncoder)), status=200)
