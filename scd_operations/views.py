import json
import logging
from dataclasses import asdict
from datetime import timedelta
from uuid import UUID

from django.http import JsonResponse
from django.shortcuts import redirect
from dotenv import find_dotenv, load_dotenv
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from auth_helper.common import get_redis
from auth_helper.utils import requires_scopes
from common.data_definitions import (
    FLIGHTBLENDER_READ_SCOPE,
    OPERATION_STATES,
    OPERATION_STATES_LOOKUP,
)
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from common.utils import EnhancedJSONEncoder
from scd_operations.data_definitions import FlightDeclarationCreationPayload

from . import dss_scd_helper
from .flight_planning_data_definitions import (
    FlightPlanningInjectionData,
    FlightPlanningStatusResponse,
    FlightPlanningTestStatus,
)
from .scd_data_definitions import (
    CapabilitiesResponse,
    CompositeOperationalIntentPayload,
    OperationalIntentState,
    OperationalIntentStorage,
    OperationalIntentSubmissionStatus,
    OperationalIntentUSSDetails,
    SCDTestStatusResponse,
    USSCapabilitiesResponseEnum,
)
from .scd_test_harness_helper import (
    FlightPlanningDataProcessor,
    FlightPlantoOperationalIntentProcessor,
    SCDTestHarnessHelper,
    failed_planning_response,
    flight_planning_deletion_failure_response,
    flight_planning_deletion_success_response,
    not_planned_activated_higher_priority_planning_response,
    not_planned_activated_planning_response,
    not_planned_already_planned_planning_response,
    not_planned_planning_response,
    planned_off_nominal_planning_response,
    planned_planning_response,
    planned_test_injection_response,
    ready_to_fly_planning_response,
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
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def scd_capabilities(request):
    return redirect(scd_test_capabilities)


# Flight Planning Close Flight Plan
@api_view(["PUT", "DELETE"])
@requires_scopes(["interuss.flight_planning.plan"])
def upsert_close_flight_plan(request, flight_plan_id):
    # Parse the incoming flight planning data
    # view_name = request.resolver_match.view_name
    # uspace_test = True if "u-space" in view_name else False

    # flight_details_storage = "flight_details:" + str(flight_plan_id)
    my_operational_intent_parser = dss_scd_helper.OperationalIntentReferenceHelper()
    my_scd_dss_helper = dss_scd_helper.SCDOperations()
    my_geo_json_converter = dss_scd_helper.VolumesConverter()
    my_volumes_validator = dss_scd_helper.VolumesValidator()
    my_database_writer = FlightBlenderDatabaseWriter()
    my_database_reader = FlightBlenderDatabaseReader()

    operation_id_str = str(flight_plan_id)
    logger.info(f"Processing -> {operation_id_str}")

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

        my_geo_json_converter.convert_volumes_to_geojson(volumes=flight_planning_volumes)
        view_rect_bounds = my_geo_json_converter.get_bounds()
        view_rect_bounds_storage = ",".join([str(i) for i in view_rect_bounds])
        view_r_bounds = ",".join(map(str, view_rect_bounds))

        # Check if operational intent exists in Flight Blender
        my_test_harness_helper = SCDTestHarnessHelper()
        flight_plan_exists_in_flight_blender = my_test_harness_helper.check_if_same_flight_id_exists(operation_id=operation_id_str)
        # Create a payload for notification
        flight_planning_notification_payload = flight_planning_data
        generated_operational_intent_state = my_flight_plan_op_intent_bridge.generate_operational_intent_state_from_planning_information()
        # Flight plan exists in Flight Blender and the new state is off nominal or contingent
        if flight_plan_exists_in_flight_blender and generated_operational_intent_state in ["Activated", "Nonconforming"]:
            # Operational intent exists, update the operational intent based on SCD rules. Get the detail of the existing / stored operational intent

            existing_op_int_details = my_operational_intent_parser.parse_stored_operational_intent_details(operation_id=operation_id_str)
            flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id_str)
            if not flight_declaration:
                failed_planning_response.notes = "Flight Declaration with ID %s not found in Flight Blender" % operation_id_str

                return Response(
                    json.loads(json.dumps(asdict(failed_planning_response), cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )

            flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_obj(
                flight_declaration=flight_declaration
            )
            current_state = flight_declaration.state
            current_state_str = OPERATION_STATES[current_state][1]
            # ID of the operational intent reference stored in the DSS
            dss_operational_intent_reference_id = flight_operational_intent_reference.id
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
                subscription_id=stored_operational_intent_details.reference.subscription_id,
                deconfliction_check=deconfliction_check,
                priority=scd_test_data.intended_flight.astm_f3548_21.priority,
                ovn=stored_operational_intent_details.reference.ovn,
            )

            if operational_intent_update_job.status == 200:
                # The operational intent update in the DSS is successful, update storage
                # Update the redis storage for operational intent details so that when the USS endpoint is queried it will reflect the most updated state.

                # Notify the subscribers that the operational intent has been updated

                # Update the OVN in the Flight Blender database
                flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_id(
                    stored_operational_intent_details.reference.id
                )
                my_database_writer.update_flight_operational_intent_reference_ovn(
                    flight_operational_intent_reference=flight_operational_intent_reference,
                    ovn=operational_intent_update_job.dss_response.operational_intent_reference.ovn,
                )

                my_scd_dss_helper.process_peer_uss_notifications(
                    all_subscribers=operational_intent_update_job.dss_response.subscribers,
                    operational_intent_details=flight_planning_notification_payload,
                    operational_intent_reference=operational_intent_update_job.dss_response.operational_intent_reference,
                    operational_intent_id=dss_operational_intent_reference_id,
                )

                if generated_operational_intent_state == "Activated":
                    # The current state is activated and the original state was also activated
                    ready_to_fly_planning_response.notes = "Created Operational Intent ID {operational_intent_id}".format(
                        operational_intent_id=dss_operational_intent_reference_id
                    )

                    # update the state to Activated
                    my_database_writer.update_flight_operation_state(flight_declaration_id=operation_id_str, state=2)

                    # Write Operational Intent Details
                    # my_database_writer.create_flight_operational_intent_details_with_submitted_operational_intent(
                    #     flight_declaration=flight_declaration,
                    #     operational_intent_details_payload=operational_intent_update_job.dss_response,)

                    # Write Operational Intent Reference
                    my_database_writer.create_flight_operational_intent_reference_with_submitted_operational_intent(
                        flight_declaration=flight_declaration,
                        operational_intent_reference_payload=operational_intent_update_job.dss_response.operational_intent_reference,
                    )

                    new_updated_operational_intent_full_details = OperationalIntentStorage(
                        bounds=view_r_bounds,
                        start_time=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                        end_time=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                        alt_max=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_upper.value,
                        alt_min=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_lower.value,
                        success_response=asdict(operational_intent_update_job.dss_response),
                        operational_intent_details=asdict(flight_planning_notification_payload),
                    )
                    new_updated_operational_intent_full_details = CompositeOperationalIntentPayload(
                        bounds=view_r_bounds,
                        start_time=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                        end_time=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                        alt_max=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_upper.value,
                        alt_min=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_lower.value,
                        operational_intent_reference_id=asdict(operational_intent_update_job.dss_response),
                        operational_intent_details_id=asdict(flight_planning_notification_payload),
                    )

                    update_operational_intent_response = Response(
                        json.loads(json.dumps(ready_to_fly_planning_response, cls=EnhancedJSONEncoder)),
                        status=status.HTTP_200_OK,
                    )

                elif generated_operational_intent_state == "Nonconforming":
                    # Update the declaration to non-conforming
                    my_database_writer.update_flight_operation_state(flight_declaration_id=operation_id_str, state=3)
                    existing_op_int_details.operational_intent_details.off_nominal_volumes = scd_test_data.intended_flight.basic_information.area
                    existing_op_int_details.success_response.operational_intent_reference.state = OperationalIntentState.Nonconforming
                    existing_op_int_details.operational_intent_details.state = OperationalIntentState.Nonconforming
                    new_updated_operational_intent_full_details = existing_op_int_details
                    # Remove outline circle from off-nominal volumes

                    update_operational_intent_response = Response(
                        json.loads(json.dumps(planned_off_nominal_planning_response, cls=EnhancedJSONEncoder)),
                        status=status.HTTP_200_OK,
                    )

                # r.set(
                #     flight_opint_key,
                #     json.dumps(asdict(new_updated_operational_intent_full_details)),
                # )
                # r.expire(name=flight_opint_key, time=opint_subscription_end_time)

                return update_operational_intent_response

            elif operational_intent_update_job.status == 999:
                # The update cannot be sent to the DSS, there is additional information that needs to be sent
                logger.info("Flight not sent to DSS..")
                if flight_plan_exists_in_flight_blender and operational_intent_update_job.additional_information.check_id.value == "B":
                    if operational_intent_update_job.additional_information.tentative_flight_plan_processing_response.value == "OkToFly":
                        return Response(
                            json.loads(
                                json.dumps(
                                    not_planned_activated_higher_priority_planning_response,
                                    cls=EnhancedJSONEncoder,
                                )
                            ),
                            status=status.HTTP_200_OK,
                        )
                    else:
                        return Response(
                            json.loads(
                                json.dumps(
                                    not_planned_activated_planning_response,
                                    cls=EnhancedJSONEncoder,
                                )
                            ),
                            status=status.HTTP_200_OK,
                        )
                elif scd_test_data.intended_flight.astm_f3548_21.priority == 100:
                    # Updated cannot be processed / sent to the DSS
                    return Response(
                        json.loads(
                            json.dumps(
                                not_planned_activated_higher_priority_planning_response,
                                cls=EnhancedJSONEncoder,
                            )
                        ),
                        status=status.HTTP_200_OK,
                    )
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
            flight_declaration_creation = FlightDeclarationCreationPayload(
                id=operation_id_str,
                operational_intent=asdict(flight_planning_data),
                flight_declaration_raw_geojson=my_geo_json_converter.geo_json,
                bounds=view_rect_bounds_storage,
                aircraft_id="0000",
                state=OPERATION_STATES_LOOKUP[generated_operational_intent_state],
            )

            flight_declaration = my_database_writer.create_flight_declaration(flight_declaration_creation=flight_declaration_creation)

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
                # Successfully submitted to the DSS, save the operational intent in DB

                logger.info("Flight with declaration id {flight_declaration_id} created".format(flight_declaration_id=operation_id_str))
                flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id_str)

                flight_planning_data.state = generated_operational_intent_state

                # Store flight DSS response and operational intent reference
                logger.info("Creating operational details in DB...")

                _operational_intent_details = OperationalIntentUSSDetails(
                    volumes=flight_planning_notification_payload.volumes,
                    off_nominal_volumes=flight_planning_notification_payload.off_nominal_volumes,
                    priority=flight_planning_notification_payload.priority,
                )
                flight_operational_intent_detail = my_database_writer.create_flight_operational_intent_details_with_submitted_operational_intent(
                    flight_declaration=flight_declaration, operational_intent_details_payload=_operational_intent_details
                )
                logger.info("Creating operational reference in DB...")
                flight_operational_intent_reference = my_database_writer.create_flight_operational_intent_reference_with_submitted_operational_intent(
                    flight_declaration=flight_declaration,
                    operational_intent_reference_payload=flight_planning_submission.dss_response.operational_intent_reference,
                )

                composite_operational_intent_payload = CompositeOperationalIntentPayload(
                    bounds=view_r_bounds,
                    start_datetime=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                    end_datetime=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                    alt_max=50,
                    alt_min=25,
                    operational_intent_reference_id=str(flight_operational_intent_reference.id),
                    operational_intent_details_id=str(flight_operational_intent_detail.id),
                )

                my_database_writer.create_or_update_composite_operational_intent(
                    flight_declaration=flight_declaration,
                    composite_operational_intent=composite_operational_intent_payload,
                )
                # Notify the subscribers that the operational intent has been created
                my_scd_dss_helper.process_peer_uss_notifications(
                    all_subscribers=flight_planning_submission.dss_response.subscribers,
                    operational_intent_details=flight_planning_notification_payload,
                    operational_intent_reference=flight_planning_submission.dss_response.operational_intent_reference,
                    operational_intent_id=flight_planning_submission.operational_intent_id,
                )

                # End store flight DSS
                planned_test_injection_response.operational_intent_id = flight_planning_submission.operational_intent_id
                # Create a flight declaration with operation id
                # End create operational intent

            elif flight_planning_submission.status == "conflict_with_flight":
                if flight_plan_exists_in_flight_blender:
                    if generated_operational_intent_state == "Accepted":
                        return Response(
                            json.loads(json.dumps(asdict(not_planned_already_planned_planning_response), cls=EnhancedJSONEncoder)),
                            status=status.HTTP_200_OK,
                        )
                return Response(
                    json.loads(json.dumps(asdict(not_planned_planning_response), cls=EnhancedJSONEncoder)),
                    status=status.HTTP_200_OK,
                )

            elif flight_planning_submission.status in ["failure", "peer_uss_data_sharing_issue"]:
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
        my_database_reader = FlightBlenderDatabaseReader()
        flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_id(
            flight_declaration_id=operation_id_str
        )

        if flight_operational_intent_reference:
            ovn = flight_operational_intent_reference.ovn
            opint_id = flight_operational_intent_reference.id
            ovn_opint = {"ovn_id": ovn, "opint_id": opint_id, "flight_declaration_id": operation_id_str}
            # logger.info("Deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))

            deletion_response = my_scd_dss_helper.delete_operational_intent(dss_operational_intent_ref_id=str(opint_id), ovn=ovn)
            if deletion_response.status == 200:
                logger.info("Success in deleting operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))

                my_database_writer.delete_flight_declaration(flight_declaration_id=operation_id_str)

                logging.info("-- Deleted operational inent {opint_id} with Flight declaration ID {flight_declaration_id}".format(**ovn_opint))
                flight_planning_deletion_response = flight_planning_deletion_success_response
            else:
                logger.info("Failed to delete operational intent {opint_id} with ovn {ovn_id}".format(**ovn_opint))
                logger.error(deletion_response.status)
                flight_planning_deletion_response = flight_planning_deletion_failure_response

        else:
            flight_planning_deletion_response = flight_planning_deletion_failure_response

        return Response(
            json.loads(json.dumps(flight_planning_deletion_response, cls=EnhancedJSONEncoder)),
            status=status.HTTP_200_OK,
        )


@api_view(["GET"])
@requires_scopes(["interuss.flight_planning.direct_automated_test"])
def flight_planning_status(request):
    # view_name = request.resolver_match.view_name
    # uspace_test = True if "u-space" in view_name else False
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
    # view_name = request.resolver_match.view_name
    # uspace_test = True if "u-space" in view_name else False
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
