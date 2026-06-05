import json
from dataclasses import asdict
from typing import Any
from uuid import UUID

import asyncio

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from flight_blender.api.dependencies import require_scopes
from flight_blender.common.utils import EnhancedJSONEncoder
from flight_blender.scd.data_definitions import FlightDeclarationCreationPayload
from flight_blender.scd.flight_planning_data_definitions import FlightPlanningInjectionData, FlightPlanningStatusResponse, FlightPlanningTestStatus
from flight_blender.scd.scd_data_definitions import (
    CapabilitiesResponse,
    CompositeOperationalIntentPayload,
    OperationalIntentState,
    OperationalIntentSubmissionStatus,
    OperationalIntentUSSDetails,
    SCDTestStatusResponse,
    USSCapabilitiesResponseEnum,
)

router = APIRouter(prefix="/scd")


# ── sync helpers ─────────────────────────────────────────────────────────────


def _do_scd_test_status() -> dict:
    status = SCDTestStatusResponse(status="Ready", version="latest")
    return json.loads(json.dumps(status, cls=EnhancedJSONEncoder))


def _do_scd_test_capabilities() -> dict:
    status = CapabilitiesResponse(
        capabilities=[
            USSCapabilitiesResponseEnum.BasicStrategicConflictDetection,
            USSCapabilitiesResponseEnum.FlightAuthorisationValidation,
            USSCapabilitiesResponseEnum.HighPriorityFlights,
        ]
    )
    return json.loads(json.dumps(status, cls=EnhancedJSONEncoder))


def _do_flight_planning_status() -> dict:
    status = FlightPlanningTestStatus(
        status=FlightPlanningStatusResponse.Ready,
        system_version="v0.1",
        api_name="Flight Planning Automated Testing Interface",
        api_version="latest",
    )
    return json.loads(json.dumps(status, cls=EnhancedJSONEncoder))


def _do_flight_planning_clear_area(request_data: dict) -> tuple[dict, int]:
    from flight_blender.scd.utils import DSSAreaClearHandler

    try:
        request_id = request_data["request_id"]
        extent_raw = request_data["extent"]
    except KeyError as ke:
        return {"result": "Could not parse clear area payload, expected key %s not found " % ke}, 400
    handler = DSSAreaClearHandler(request_id=request_id)
    clear_area_response = handler.clear_area_request(extent_raw=extent_raw)
    return json.loads(json.dumps(clear_area_response, cls=EnhancedJSONEncoder)), 200


def _do_upsert_flight_plan(flight_plan_id: str, request_data: dict) -> tuple[dict, int]:
    from flight_blender.common.data_definitions import OPERATION_STATES_LOOKUP
    from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade
    from flight_blender.scd import dss_scd_helper
    from flight_blender.scd.scd_test_harness_helper import (
        FlightPlanningDataProcessor,
        FlightPlantoOperationalIntentProcessor,
        SCDTestHarnessHelper,
        failed_planning_response,
        not_planned_activated_higher_priority_planning_response,
        not_planned_activated_planning_response,
        not_planned_already_planned_planning_response,
        not_planned_planning_response,
        planned_off_nominal_planning_response,
        planned_planning_response,
        planned_test_injection_response,
        ready_to_fly_planning_response,
    )
    from flight_blender.scd.utils import DSSAreaClearHandler, OperatorRegistrationNumberValidator, UAVSerialNumberValidator

    my_operational_intent_parser = dss_scd_helper.OperationalIntentReferenceHelper()
    my_scd_dss_helper = dss_scd_helper.SCDOperations()
    my_geo_json_converter = dss_scd_helper.VolumesConverter()
    my_volumes_validator = dss_scd_helper.VolumesValidator()
    my_database_writer = SyncDatabaseFacade()
    my_database_reader = SyncDatabaseFacade()

    operation_id_str = str(flight_plan_id)

    scd_test_data = request_data
    try:
        my_flight_plan_processor = FlightPlanningDataProcessor(incoming_flight_information=scd_test_data)
    except KeyError as ke:
        return {"result": "Could not parse flight plan payload: %s" % ke}, 500
    scd_test_data = my_flight_plan_processor.process_incoming_flight_plan_data()
    my_flight_plan_op_intent_bridge = FlightPlantoOperationalIntentProcessor(flight_planning_request=scd_test_data)

    flight_planning_off_nominal_volumes = []
    flight_planning_volumes = scd_test_data.intended_flight.basic_information.area
    flight_planning_priority = scd_test_data.intended_flight.astm_f3548_21.priority if scd_test_data.intended_flight.astm_f3548_21.priority else 0
    flight_planning_uas_state = scd_test_data.intended_flight.basic_information.uas_state.value
    flight_planning_usage_state = scd_test_data.intended_flight.basic_information.usage_state.value

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
        return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

    volumes_valid = my_volumes_validator.validate_volumes(volumes=scd_test_data.intended_flight.basic_information.area)
    if not volumes_valid:
        return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

    my_serial_number_validator = UAVSerialNumberValidator(serial_number=scd_test_data.intended_flight.uspace_flight_authorisation.uas_serial_number)
    my_reg_number_validator = OperatorRegistrationNumberValidator(
        operator_registration_number=scd_test_data.intended_flight.uspace_flight_authorisation.operator_id
    )

    if not my_serial_number_validator.is_valid():
        return json.loads(json.dumps(asdict(not_planned_planning_response), cls=EnhancedJSONEncoder)), 200

    if not my_reg_number_validator.is_valid():
        return json.loads(json.dumps(asdict(not_planned_planning_response), cls=EnhancedJSONEncoder)), 200

    auth_token = my_scd_dss_helper.get_auth_token()
    if not auth_token or "error" in auth_token:
        return json.loads(json.dumps(asdict(failed_planning_response), cls=EnhancedJSONEncoder)), 200

    my_geo_json_converter.convert_volumes_to_geojson(volumes=flight_planning_volumes)
    view_rect_bounds = my_geo_json_converter.get_bounds()
    view_rect_bounds_storage = ",".join([str(i) for i in view_rect_bounds])

    my_test_harness_helper = SCDTestHarnessHelper()
    flight_plan_exists_in_flight_blender = my_test_harness_helper.check_if_same_flight_id_exists(operation_id=operation_id_str)

    flight_planning_notification_payload = flight_planning_data
    generated_operational_intent_state = my_flight_plan_op_intent_bridge.generate_operational_intent_state_from_planning_information()

    if flight_plan_exists_in_flight_blender and generated_operational_intent_state in ["Activated", "Nonconforming"]:
        from flight_blender.common.data_definitions import OPERATION_STATES

        existing_op_int_details = my_operational_intent_parser.parse_stored_operational_intent_details(operation_id=operation_id_str)
        flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id_str)
        if not flight_declaration:
            failed_planning_response.notes = "Flight Declaration with ID %s not found in Flight Blender" % operation_id_str
            return json.loads(json.dumps(asdict(failed_planning_response), cls=EnhancedJSONEncoder)), 200

        flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_obj(
            flight_declaration=flight_declaration
        )
        current_state = flight_declaration.state
        current_state_str = OPERATION_STATES[current_state][1]
        dss_operational_intent_reference_id = str(flight_operational_intent_reference.id)
        stored_operational_intent_details = my_operational_intent_parser.parse_and_load_stored_flight_operational_intent_reference(
            operation_id=operation_id_str
        )
        provided_volumes_off_nominal_volumes = scd_test_data.intended_flight.basic_information.area
        deconfliction_check = True

        if current_state_str in ["Accepted", "Activated"] and generated_operational_intent_state == "Nonconforming":
            deconfliction_check = False
        elif current_state_str == "Activated" and generated_operational_intent_state == "Activated":
            deconfliction_check = True

        operational_intent_update_job = my_scd_dss_helper.update_specified_operational_intent_reference(
            operational_intent_ref_id=str(stored_operational_intent_details.reference.id),
            extents=provided_volumes_off_nominal_volumes,
            new_state=generated_operational_intent_state,
            current_state=current_state_str,
            subscription_id=stored_operational_intent_details.reference.subscription_id,
            deconfliction_check=deconfliction_check,
            priority=scd_test_data.intended_flight.astm_f3548_21.priority,
            ovn=stored_operational_intent_details.reference.ovn,
        )

        if operational_intent_update_job.status == 200:
            flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_id(
                stored_operational_intent_details.reference.id
            )
            flight_declaration = flight_operational_intent_reference.declaration
            flight_operational_intent_details = my_database_reader.get_operational_intent_details_by_flight_declaration_id(
                declaration_id=str(flight_declaration.id)
            )

            my_database_writer.update_flight_operational_intent_reference(
                flight_operational_intent_reference=flight_operational_intent_reference,
                update_operational_intent_reference=operational_intent_update_job.dss_response.operational_intent_reference,
            )
            updated_flight_operational_intent_details = OperationalIntentUSSDetails(
                volumes=flight_planning_volumes or [],  # type: ignore[arg-type]
                off_nominal_volumes=flight_planning_off_nominal_volumes,
                priority=flight_planning_priority,
            )
            my_database_writer.update_flight_operational_intent_details(
                flight_operational_intent_detail=flight_operational_intent_details,
                operational_intent_details=updated_flight_operational_intent_details,
            )
            my_scd_dss_helper.process_peer_uss_notifications(
                all_subscribers=operational_intent_update_job.dss_response.subscribers,
                operational_intent_details=flight_planning_notification_payload,
                operational_intent_reference=operational_intent_update_job.dss_response.operational_intent_reference,
                operational_intent_id=dss_operational_intent_reference_id,
            )

            if generated_operational_intent_state == "Activated":
                ready_to_fly_planning_response.notes = "Created Operational Intent ID {operational_intent_id}".format(
                    operational_intent_id=dss_operational_intent_reference_id
                )
                my_database_writer.update_flight_operation_state(flight_declaration_id=operation_id_str, state=2)
                my_database_writer.create_flight_operational_intent_reference_subscribers(
                    flight_declaration=flight_declaration,
                    subscribers=operational_intent_update_job.dss_response.subscribers,
                )
                my_database_writer.create_or_update_composite_operational_intent(
                    flight_declaration=flight_declaration,
                    composite_operational_intent_payload=CompositeOperationalIntentPayload(
                        bounds=view_rect_bounds_storage,
                        start_datetime=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                        end_datetime=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                        alt_max=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_upper.value,
                        alt_min=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_lower.value,
                        operational_intent_reference_id=str(flight_operational_intent_reference.id),
                        operational_intent_details_id=str(flight_operational_intent_details.id),
                    ),
                )
                return json.loads(json.dumps(ready_to_fly_planning_response, cls=EnhancedJSONEncoder)), 200

            elif generated_operational_intent_state == "Nonconforming":
                my_database_writer.update_flight_operation_state(flight_declaration_id=operation_id_str, state=3)
                existing_op_int_details.operational_intent_details.off_nominal_volumes = scd_test_data.intended_flight.basic_information.area
                existing_op_int_details.success_response.operational_intent_reference.state = OperationalIntentState.Nonconforming
                existing_op_int_details.operational_intent_details.state = OperationalIntentState.Nonconforming
                my_database_writer.create_or_update_composite_operational_intent(
                    flight_declaration=flight_declaration,
                    composite_operational_intent_payload=existing_op_int_details,
                )
                return json.loads(json.dumps(planned_off_nominal_planning_response, cls=EnhancedJSONEncoder)), 200

        elif operational_intent_update_job.status == 999:
            if flight_plan_exists_in_flight_blender and operational_intent_update_job.additional_information.check_id.value == "B":
                if operational_intent_update_job.additional_information.tentative_flight_plan_processing_response.value == "OkToFly":
                    return json.loads(json.dumps(not_planned_activated_higher_priority_planning_response, cls=EnhancedJSONEncoder)), 200
                else:
                    return json.loads(json.dumps(not_planned_activated_planning_response, cls=EnhancedJSONEncoder)), 200
            elif scd_test_data.intended_flight.astm_f3548_21.priority == 100:
                return json.loads(json.dumps(not_planned_activated_higher_priority_planning_response, cls=EnhancedJSONEncoder)), 200
            return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200
        else:
            return json.loads(json.dumps(failed_planning_response, cls=EnhancedJSONEncoder)), 200
    else:
        flight_declaration_creation = FlightDeclarationCreationPayload(
            id=operation_id_str,
            operational_intent=json.loads(json.dumps(flight_planning_data, cls=EnhancedJSONEncoder)),
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
            return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

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
            from flight_blender.scd.dss_scd_helper import ConstraintsWriter

            flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id_str)
            flight_planning_data.state = generated_operational_intent_state

            _operational_intent_details = OperationalIntentUSSDetails(
                volumes=flight_planning_notification_payload.volumes,
                off_nominal_volumes=flight_planning_notification_payload.off_nominal_volumes,
                priority=flight_planning_notification_payload.priority,
            )
            flight_operational_intent_detail = my_database_writer.create_flight_operational_intent_details_with_submitted_operational_intent(
                flight_declaration=flight_declaration,
                operational_intent_details_payload=_operational_intent_details,
            )
            flight_operational_intent_reference = my_database_writer.create_flight_operational_intent_reference_with_submitted_operational_intent(
                flight_declaration=flight_declaration,
                operational_intent_reference_payload=flight_planning_submission.dss_response.operational_intent_reference,
            )
            my_database_writer.create_flight_operational_intent_reference_subscribers(
                flight_declaration=flight_declaration,
                subscribers=flight_planning_submission.dss_response.subscribers,
            )
            composite_payload = CompositeOperationalIntentPayload(
                bounds=view_rect_bounds_storage,
                start_datetime=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                end_datetime=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                alt_max=50,
                alt_min=25,
                operational_intent_reference_id=str(flight_operational_intent_reference.id),
                operational_intent_details_id=str(flight_operational_intent_detail.id),
            )

            if flight_planning_submission.constraints:
                my_constraints_writer = ConstraintsWriter()
                my_constraints_writer.write_nearby_constraints(
                    flight_declaration=flight_declaration,
                    constraints=flight_planning_submission.constraints,
                )

            my_database_writer.create_or_update_composite_operational_intent(
                flight_declaration=flight_declaration,
                composite_operational_intent_payload=composite_payload,
            )
            my_scd_dss_helper.process_peer_uss_notifications(
                all_subscribers=flight_planning_submission.dss_response.subscribers,
                operational_intent_details=flight_planning_notification_payload,
                operational_intent_reference=flight_planning_submission.dss_response.operational_intent_reference,
                operational_intent_id=flight_planning_submission.operational_intent_id,
            )
            planned_test_injection_response.operational_intent_id = flight_planning_submission.operational_intent_id

        elif flight_planning_submission.status == "conflict_with_flight":
            if flight_plan_exists_in_flight_blender:
                if generated_operational_intent_state == "Accepted":
                    return json.loads(json.dumps(asdict(not_planned_already_planned_planning_response), cls=EnhancedJSONEncoder)), 200
            return json.loads(json.dumps(asdict(not_planned_planning_response), cls=EnhancedJSONEncoder)), 200

        elif flight_planning_submission.status in ["failure", "peer_uss_data_sharing_issue"]:
            if flight_planning_submission.status_code == 408:
                return json.loads(json.dumps(asdict(not_planned_planning_response), cls=EnhancedJSONEncoder)), 200
            else:
                return json.loads(json.dumps(asdict(failed_planning_response), cls=EnhancedJSONEncoder)), 200

        if flight_planning_usage_state == "Planned":
            return json.loads(json.dumps(asdict(ready_to_fly_planning_response), cls=EnhancedJSONEncoder)), 200
        else:
            return json.loads(json.dumps(asdict(planned_planning_response), cls=EnhancedJSONEncoder)), 200


def _do_delete_flight_plan(flight_plan_id: str) -> tuple[dict, int]:
    from dataclasses import asdict

    from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade
    from flight_blender.scd import dss_scd_helper
    from flight_blender.scd.scd_test_harness_helper import (
        flight_planning_deletion_failure_response,
        flight_planning_deletion_success_response,
    )

    operation_id_str = str(flight_plan_id)
    my_scd_dss_helper = dss_scd_helper.SCDOperations()
    my_database_reader = SyncDatabaseFacade()
    my_database_writer = SyncDatabaseFacade()

    flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_id(
        flight_declaration_id=operation_id_str
    )

    if flight_operational_intent_reference:
        ovn = flight_operational_intent_reference.ovn
        opint_id = flight_operational_intent_reference.id
        deletion_response = my_scd_dss_helper.delete_operational_intent(dss_operational_intent_ref_id=str(opint_id), ovn=ovn)
        if deletion_response.status == 200:
            my_database_writer.delete_flight_declaration(flight_declaration_id=operation_id_str)
            return json.loads(json.dumps(flight_planning_deletion_success_response, cls=EnhancedJSONEncoder)), 200
        else:
            return json.loads(json.dumps(flight_planning_deletion_failure_response, cls=EnhancedJSONEncoder)), 200
    else:
        return json.loads(json.dumps(flight_planning_deletion_failure_response, cls=EnhancedJSONEncoder)), 200


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("/v1/status")
async def scd_test_status(_auth: Any = Depends(require_scopes(["utm.inject_test_data"]))):
    data = await asyncio.to_thread(_do_scd_test_status)
    return JSONResponse(data, status_code=200)


@router.get("/v1/capabilities")
async def scd_test_capabilities(_auth: Any = Depends(require_scopes(["utm.inject_test_data"]))):
    data = await asyncio.to_thread(_do_scd_test_capabilities)
    return JSONResponse(data, status_code=200)


@router.get("/flight_planning/status")
@router.get("/flight_planning/u_space/status")
async def flight_planning_status(_auth: Any = Depends(require_scopes(["interuss.flight_planning.direct_automated_test"]))):
    data = await asyncio.to_thread(_do_flight_planning_status)
    return JSONResponse(data, status_code=200)


@router.post("/flight_planning/clear_area_requests")
@router.post("/flight_planning/u_space/clear_area_requests")
async def flight_planning_clear_area_request(
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.direct_automated_test"])),
):
    data, status_code = await asyncio.to_thread(_do_flight_planning_clear_area, body)
    return JSONResponse(data, status_code=status_code)


@router.put("/flight_planning/flight_plans/{flight_plan_id}")
@router.put("/flight_planning/u_space/flight_plans/{flight_plan_id}")
async def upsert_flight_plan(
    flight_plan_id: UUID,
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
):
    data, status_code = await asyncio.to_thread(_do_upsert_flight_plan, str(flight_plan_id), body)
    return JSONResponse(data, status_code=status_code)


@router.delete("/flight_planning/flight_plans/{flight_plan_id}")
@router.delete("/flight_planning/u_space/flight_plans/{flight_plan_id}")
async def delete_flight_plan(
    flight_plan_id: UUID,
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
):
    data, status_code = await asyncio.to_thread(_do_delete_flight_plan, str(flight_plan_id))
    return JSONResponse(data, status_code=status_code)
