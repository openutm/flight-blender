import asyncio
import json
import time
import uuid
from dataclasses import asdict
from enum import Enum
from typing import Any

import arrow
from dacite import Config, from_dict
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse, Response
from loguru import logger

from flight_blender.api.dependencies import require_scopes
from flight_blender.utils.json_codecs import EnhancedJSONEncoder

router = APIRouter(prefix="/uss")


# ── sync helpers ─────────────────────────────────────────────────────────────


def _do_peer_uss_report_notification(request_data: dict) -> tuple[dict, int]:
    from flight_blender.domain_types.uss import ErrorReport

    try:
        error_report = from_dict(data_class=ErrorReport, data=request_data, config=Config(cast=[Enum]))
    except Exception as e:
        return {"message": str(e)}, 500
    report_id = str(uuid.uuid4())
    error_report.report_id = report_id
    return json.loads(json.dumps(asdict(error_report), cls=EnhancedJSONEncoder)), 201


def _do_uss_operational_intent_details(opint_id: str) -> tuple[dict, int]:
    from flight_blender.domain_types.uss import (
        OperationalIntentDetails,
        OperationalIntentDetailsUSSResponse,
        OperationalIntentNotFoundResponse,
        OperationalIntentReferenceDSSResponse,
        OperationalIntentUSSDetails,
        Time,
    )
    from flight_blender.repositories.sync_facade import SyncDatabaseFacade  # TODO: replace with async repo

    my_database_reader = SyncDatabaseFacade()
    flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_id(opint_id)
    if not flight_operational_intent_reference:
        not_found = OperationalIntentNotFoundResponse(message="Requested Operational intent with id %s not found" % opint_id)
        return json.loads(json.dumps(not_found, cls=EnhancedJSONEncoder)), 404

    operational_intent_id = str(flight_operational_intent_reference.declaration.id)
    stored_details = my_database_reader.get_composite_operational_intent_by_declaration_id(flight_declaration_id=operational_intent_id)
    details_full = stored_details.operational_intent_details
    reference_full = stored_details.operational_intent_reference

    stored_volumes = json.loads(details_full.volumes)
    for v in stored_volumes:
        if "outline_circle" in v["volume"].keys():
            if not v["volume"]["outline_circle"]:
                v["volume"].pop("outline_circle")

    stored_off_nominal_volumes = json.loads(details_full.off_nominal_volumes)
    for v in stored_off_nominal_volumes:
        if "outline_circle" in v["volume"].keys():
            if not v["volume"]["outline_circle"]:
                v["volume"].pop("outline_circle")

    reference = OperationalIntentReferenceDSSResponse(
        id=str(reference_full.id),
        manager=reference_full.manager,
        uss_availability=reference_full.uss_availability,
        version=int(reference_full.version),
        state=reference_full.state,
        ovn=reference_full.ovn,
        time_start=Time(format="RFC3339", value=reference_full.time_start.isoformat()),
        time_end=Time(format="RFC3339", value=reference_full.time_end.isoformat()),
        uss_base_url=reference_full.uss_base_url,
        subscription_id=reference_full.subscription_id,
    )
    details = OperationalIntentUSSDetails(
        volumes=stored_volumes,
        priority=details_full.priority,
        off_nominal_volumes=stored_off_nominal_volumes,
    )
    operational_intent = OperationalIntentDetailsUSSResponse(reference=reference, details=details)
    response = OperationalIntentDetails(operational_intent=operational_intent)
    return json.loads(json.dumps(response, cls=EnhancedJSONEncoder)), 200


def _do_uss_update_opint_details(request_data: dict) -> tuple[dict, int]:
    from flight_blender.domain_types.scd import CompositeOperationalIntentPayload
    from flight_blender.domain_types.uss import UpdateChangedOpIntDetailsPost
    from flight_blender.repositories.sync_facade import SyncDatabaseFacade  # TODO: replace with async repo
    from flight_blender.clients.dss_scd_client import VolumesConverter

    database_writer = SyncDatabaseFacade()
    my_geo_json_converter = VolumesConverter()

    try:
        incoming_update_payload = from_dict(data_class=UpdateChangedOpIntDetailsPost, data=request_data)
    except Exception as e:
        return {"message": str(e)}, 500
    operation_id_str = incoming_update_payload.operational_intent_id

    if incoming_update_payload.operational_intent:
        updated_operational_intent_reference = incoming_update_payload.operational_intent.reference
        update_operational_intent_details = incoming_update_payload.operational_intent.details

        database_writer.create_or_update_peer_operational_intent_details(
            peer_operational_intent_id=operation_id_str,
            operational_intent_details=update_operational_intent_details,
        )
        database_writer.create_or_update_peer_operational_intent_reference(
            peer_operational_intent_reference_id=operation_id_str,
            peer_operational_intent_reference=updated_operational_intent_reference,
        )

        if update_operational_intent_details.volumes:
            all_volumes = update_operational_intent_details.volumes
        elif update_operational_intent_details.off_nominal_volumes:
            all_volumes = update_operational_intent_details.off_nominal_volumes
        else:
            all_volumes = []

        if all_volumes:
            start_datetime = all_volumes[0].time_start.value
            end_datetime = all_volumes[0].time_end.value

            my_geo_json_converter.convert_volumes_to_geojson(volumes=all_volumes)
            view_rect_bounds = my_geo_json_converter.get_bounds()

            operational_intent_full_details = CompositeOperationalIntentPayload(
                bounds=view_rect_bounds,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                alt_max=50,
                alt_min=25,
                operational_intent_reference_id=operation_id_str,
                operational_intent_details_id=operation_id_str,
            )
            database_writer.create_or_update_peer_composite_operational_intent(
                operation_id=operation_id_str,
                composite_operational_intent=operational_intent_full_details,
            )

    return {}, 204


def _do_uss_constraint_details(constraint_id: str) -> tuple[dict, int]:
    from flight_blender.domain_types.uss import GenericErrorResponseMessage
    from flight_blender.repositories.sync_facade import SyncDatabaseFacade  # TODO: replace with async repo

    my_database_reader = SyncDatabaseFacade()
    constraint_id_exists = my_database_reader.check_constraint_id_exists(constraint_id=constraint_id)
    if constraint_id_exists:
        constraint_details = my_database_reader.get_constraint_details(constraint_id=constraint_id)
        if constraint_details:
            return json.loads(json.dumps(constraint_details, cls=EnhancedJSONEncoder)), 200
        else:
            not_found = GenericErrorResponseMessage(message="Requested Constraint with id %s not found" % constraint_id)
            return json.loads(json.dumps(not_found, cls=EnhancedJSONEncoder)), 404
    else:
        not_found = GenericErrorResponseMessage(message="Requested Constraint with id %s not found" % constraint_id)
        return json.loads(json.dumps(not_found, cls=EnhancedJSONEncoder)), 404


def _do_uss_update_constraint_details(request_data: dict) -> int:
    from flight_blender.domain_types.constraint import PutConstraintDetailsParameters
    from flight_blender.repositories.sync_facade import SyncDatabaseFacade  # TODO: replace with async repo

    my_database_reader = SyncDatabaseFacade()
    my_database_writer = SyncDatabaseFacade()
    constraint_update_detail = from_dict(data_class=PutConstraintDetailsParameters, data=request_data)

    constraint_id = constraint_update_detail.constraint_id
    constraint_id_exists = my_database_reader.check_constraint_id_exists(constraint_id=constraint_id)
    if constraint_id_exists and constraint_update_detail.constraint:
        my_database_writer.write_constraint_details(constraint_id=constraint_id, constraint=constraint_update_detail.constraint)

    constraint_reference_exists = my_database_reader.check_constraint_reference_id_exists(constraint_reference_id=constraint_id)
    if constraint_reference_exists and constraint_update_detail.constraint:
        my_database_writer.write_constraint_reference_details(constraint=constraint_update_detail.constraint)

    return 204


def _do_get_uss_flights(view: str) -> tuple[dict, int]:
    from flight_blender.domain_types.uss import (
        GenericErrorResponseMessage,
        GetFlightsResponse,
        RIDAircraftPosition,
        RIDAircraftState,
        RIDFlight,
        RIDFormat,
        RIDHeight,
        RIDTime,
    )
    from flight_blender.services import flight_feed_svc as flight_stream_helper
    from flight_blender.services import rid_svc as view_port_ops
    from flight_blender.auth.token_cache import get_redis
    from flight_blender.repositories.sync_facade import SyncDatabaseFacade  # TODO: replace with async repo

    try:
        view_port = [float(i) for i in view.split(",")]
    except Exception:
        return asdict(GenericErrorResponseMessage(message="A view bbox is necessary with four values: minx, miny, maxx and maxy")), 400

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)
    if not view_port_valid:
        return asdict(GenericErrorResponseMessage(message="The requested view %s rectangle is not valid format: lat1,lng1,lat2,lng2" % view)), 400

    view_box = view_port_ops.build_view_port_box(view_port_coords=view_port)
    view_port_diagonal = view_port_ops.get_view_port_diagonal_length_kms(view_port_coords=view_port)
    if view_port_diagonal > 7:
        return asdict(GenericErrorResponseMessage(message="The requested view %s rectangle is too large" % view)), 413

    time.sleep(0.5)

    obs_helper = flight_stream_helper.ObservationReadOperations(redis=get_redis(), view_port_box=view_box, db_reader=SyncDatabaseFacade())
    all_flights_telemetry_data = obs_helper.get_closest_observation_for_now(now=arrow.now())

    now = arrow.now().isoformat()
    if all_flights_telemetry_data:
        rid_flights = []
        for observation_data in all_flights_telemetry_data:
            observation_data_dict = {}
            try:
                observation_data_dict = observation_data.metadata
            except KeyError as ke:
                logger.error("Error in metadata data in the stream %s" % ke)

            telemetry_data_dict = observation_data_dict["telemetry"]
            height = RIDHeight(
                distance=telemetry_data_dict["height"]["distance"],
                reference=telemetry_data_dict["height"]["reference"],
            )
            position = RIDAircraftPosition(
                lat=telemetry_data_dict["position"]["lat"],
                lng=telemetry_data_dict["position"]["lng"],
                alt=telemetry_data_dict["position"]["alt"],
                accuracy_h=telemetry_data_dict["position"]["accuracy_h"],
                accuracy_v=telemetry_data_dict["position"]["accuracy_v"],
                extrapolated=telemetry_data_dict["position"]["extrapolated"],
                pressure_altitude=telemetry_data_dict["position"]["pressure_altitude"],
                height=height,
            )
            current_state = RIDAircraftState(
                timestamp=RIDTime(
                    value=telemetry_data_dict["timestamp"]["value"],
                    format=telemetry_data_dict["timestamp"]["format"],
                ),
                timestamp_accuracy=telemetry_data_dict["timestamp_accuracy"],
                operational_status=telemetry_data_dict["operational_status"],
                position=position,
                track=telemetry_data_dict["track"],
                speed=telemetry_data_dict["speed"],
                speed_accuracy=telemetry_data_dict["speed_accuracy"],
                vertical_speed=telemetry_data_dict["vertical_speed"],
            )
            current_flight = RIDFlight(
                id=observation_data_dict["injection_id"],
                aircraft_type=observation_data_dict["aircraft_type"],
                current_state=current_state,
                simulated=True,
                recent_positions=[],
            )
            rid_flights.append(current_flight)

        all_flights = [asdict(f, dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)}) for f in rid_flights]
        rid_response = GetFlightsResponse(timestamp=RIDTime(value=now, format=RIDFormat.RFC3339), flights=all_flights)
    else:
        rid_response = GetFlightsResponse(timestamp=RIDTime(value=now, format=RIDFormat.RFC3339), flights=[])

    return json.loads(json.dumps(asdict(rid_response))), 200


def _do_get_uss_flight_details(flight_id: str) -> tuple[dict, int]:
    from flight_blender.domain_types.rid import UASID, OperatorLocation, UAClassificationEU
    from flight_blender.domain_types.uss import FlightDetailsNotFoundMessage, OperatorDetailsSuccessResponse, RIDAuthData, RIDFlightDetails
    from flight_blender.repositories.sync_facade import SyncDatabaseFacade  # TODO: replace with async repo

    my_database_reader = SyncDatabaseFacade()
    flight_details_exists = my_database_reader.check_flight_details_exist(flight_detail_id=flight_id)
    if not flight_details_exists:
        fd = FlightDetailsNotFoundMessage(message="The requested flight could not be found")
        return json.loads(json.dumps(asdict(fd))), 404

    flight_details = my_database_reader.get_flight_details_by_id(flight_detail_id=flight_id)
    _operator_location = json.loads(flight_details.operator_location)
    operator_location = from_dict(
        data_class=OperatorLocation,
        data=_operator_location,
        config=Config(cast=[Enum]),
    )

    eu_classification = None
    _eu_classification = json.loads(flight_details.eu_classification)
    if _eu_classification:
        eu_classification = UAClassificationEU(
            category=_eu_classification["category"],
            class_=_eu_classification["class"],
        )
    uas_id = None
    _uas_id = json.loads(flight_details.uas_id)
    if _uas_id:
        uas_id = UASID(
            specific_session_id=_uas_id["specific_session_id"],
            serial_number=_uas_id["serial_number"],
            registration_id=_uas_id["registration_id"],
            utm_id=_uas_id["utm_id"],
        )
    auth_data = None
    _auth_data = json.loads(flight_details.auth_data)
    if _auth_data:
        auth_data = RIDAuthData(
            format=int(_auth_data["format"]),
            data=_auth_data["data"],
        )
    f_detail = RIDFlightDetails(
        id=str(flight_details.id),
        operator_id=flight_details.operator_id,
        operator_location=operator_location,
        operation_description=flight_details.operation_description,
        auth_data=auth_data,
        uas_id=uas_id,
        eu_classification=eu_classification,
    )
    flight_details_full = OperatorDetailsSuccessResponse(details=f_detail)
    return json.loads(
        json.dumps(asdict(flight_details_full, dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)}), cls=EnhancedJSONEncoder)
    ), 200


def _do_uss_telemetry(opint_id: str) -> dict:
    from flight_blender.domain_types.uss import Time, VehicleTelemetry, VehicleTelemetryResponse

    now = arrow.now()
    five_seconds_from_now = now.shift(seconds=5)
    telemetry_response = VehicleTelemetryResponse(
        operational_intent_id=opint_id,
        telemetry=VehicleTelemetry(
            time_measured=Time(format="RFC3339", value=arrow.now().isoformat()),
            position=None,
            velocity=None,
        ),
        next_telemetry_opportunity=Time(format="RFC3339", value=five_seconds_from_now.isoformat()),
    )
    return json.loads(json.dumps(asdict(telemetry_response), cls=EnhancedJSONEncoder))


# ── routes ────────────────────────────────────────────────────────────────────


@router.post("/v1/reports")
async def peer_uss_report_notification(
    body: dict = Body(...),
    _auth: Any = Depends(
        require_scopes(
            [
                "utm.strategic_coordination",
                "utm.constraint_processing",
                "utm.constraint_management",
                "utm.conformance_monitoring_sa",
                "utm.availability_arbitration",
            ],
            allow_any=True,
        )
    ),
):
    data, status_code = await asyncio.to_thread(_do_peer_uss_report_notification, body)
    return JSONResponse(data, status_code=status_code)


@router.get("/v1/operational_intents/{opint_id}")
async def uss_operational_intent_details(
    opint_id: uuid.UUID,
    _auth: Any = Depends(require_scopes(["utm.strategic_coordination"])),
):
    data, status_code = await asyncio.to_thread(_do_uss_operational_intent_details, str(opint_id))
    return JSONResponse(data, status_code=status_code)


@router.get("/v1/operational_intents/{opint_id}/telemetry")
async def uss_opint_detail_telemetry(
    opint_id: uuid.UUID,
    _auth: Any = Depends(require_scopes(["utm.conformance_monitoring_sa"])),
):
    data = await asyncio.to_thread(_do_uss_telemetry, str(opint_id))
    return JSONResponse(data, status_code=200)


@router.post("/v1/operational_intents")
async def uss_update_opint_details(
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes(["utm.strategic_coordination"])),
):
    data, status_code = await asyncio.to_thread(_do_uss_update_opint_details, body)
    return Response(status_code=status_code)


@router.get("/v1/constraints/{constraint_id}")
async def uss_constraint_details(
    constraint_id: uuid.UUID,
    _auth: Any = Depends(require_scopes(["utm.constraint_processing"])),
):
    data, status_code = await asyncio.to_thread(_do_uss_constraint_details, str(constraint_id))
    return JSONResponse(data, status_code=status_code)


@router.post("/v1/constraints")
async def uss_update_constraint_details(
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes(["utm.constraint_processing"])),
):
    status_code = await asyncio.to_thread(_do_uss_update_constraint_details, body)
    return Response(status_code=status_code)


@router.get("/flights")
async def get_uss_flights(
    view: str | None = None,
    _auth: Any = Depends(require_scopes(["rid.display_provider"])),
):
    if not view:
        return JSONResponse({"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, status_code=400)
    data, status_code = await asyncio.to_thread(_do_get_uss_flights, view)
    return JSONResponse(data, status_code=status_code)


@router.get("/flights/{flight_id}/details")
async def get_uss_flight_details(
    flight_id: str,
    _auth: Any = Depends(require_scopes(["rid.display_provider"])),
):
    data, status_code = await asyncio.to_thread(_do_get_uss_flight_details, flight_id)
    return JSONResponse(data, status_code=status_code)
