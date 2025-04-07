import logging

from dotenv import find_dotenv, load_dotenv

from flight_blender.celery import app
from flight_feed_operations import flight_stream_helper
from scd_operations.scd_data_definitions import LatLngPoint

from . import custom_signals
from .utils import FlightBlenderConformanceEngine

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)

logger = logging.getLogger("django")


# This method conducts flight conformance checks as a async tasks
@app.task(name="check_flight_conformance")
def check_flight_conformance(flight_declaration_id: str, session_id: str, dry_run: str = "1"):
    # This method checks the conformance status for ongoing operations and sends notifications / via the notifications channel

    dry_run = True if dry_run == "1" else False
    d_run = "1" if dry_run else "0"
    my_conformance_ops = FlightBlenderConformanceEngine()

    flight_authorization_conformant = my_conformance_ops.check_flight_authorization_conformance(flight_declaration_id=flight_declaration_id)
    if flight_authorization_conformant:
        logger.info(f"Operation with {flight_declaration_id} is conformant...")
        # Basic conformance checks passed, check telemetry conformance
        check_operation_telemetry_conformance(flight_declaration_id=flight_declaration_id, dry_run=d_run)
    else:
        custom_signals.flight_authorization_non_conformance_signal.send(
            sender="check_flight_conformance",
            non_conformance_state=flight_authorization_conformant,
            flight_declaration_id=flight_declaration_id,
        )
        # Flight Declaration is not conformant
        logger.info(f"Operation with {flight_declaration_id} is not conformant...")


# This method conducts flight telemetry checks
@app.task(name="check_operation_telemetry_conformance")
def check_operation_telemetry_conformance(flight_declaration_id: str, dry_run: str = "1"):
    # This method checks the conformance status for ongoing operations and sends notifications / via the notifications channel
    dry_run = True if dry_run == "1" else False
    my_conformance_ops = FlightBlenderConformanceEngine()
    # Get Telemetry
    obs_helper = flight_stream_helper.ObservationReadOperations()
    all_flights_rid_data = obs_helper.get_flight_observations(session_id=flight_declaration_id)
    # Get the latest telemetry

    if not all_flights_rid_data:
        logger.error(f"No telemetry data found for operation {flight_declaration_id}")
        return

    distinct_messages = all_flights_rid_data if all_flights_rid_data else []

    for message in distinct_messages:
        metadata = message.metadata
        if metadata["flight_details"]["id"] == flight_declaration_id:
            lat_dd = message.latitude_dd
            lon_dd = message.longitude_dd
            altitude_m_wgs84 = message.altitude_mm
            aircraft_id = message.icao_address

            conformant_via_telemetry = my_conformance_ops.is_operation_conformant_via_telemetry(
                flight_declaration_id=flight_declaration_id,
                aircraft_id=aircraft_id,
                telemetry_location=LatLngPoint(lat=lat_dd, lng=lon_dd),
                altitude_m_wgs_84=float(altitude_m_wgs84),
            )
            if conformant_via_telemetry is True:
                pass
            else:
                logger.info(
                    "Operation with {flight_operation_id} is not conformant via telemetry failed test {conformant_via_telemetry}...".format(
                        flight_operation_id=flight_declaration_id,
                        conformant_via_telemetry=conformant_via_telemetry,
                    )
                )
                custom_signals.telemetry_non_conformance_signal.send(
                    sender="conformant_via_telemetry",
                    non_conformance_state=conformant_via_telemetry,
                    flight_declaration_id=flight_declaration_id,
                )
            break
