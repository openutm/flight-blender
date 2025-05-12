import json
import logging
from os import environ as env

from dacite import from_dict
from django.core.management.base import BaseCommand, CommandError
from dotenv import find_dotenv, load_dotenv
from shapely.geometry import Point, Polygon

from auth_helper.common import get_redis
from common.data_definitions import OPERATION_STATES
from common.database_operations import FlightBlenderDatabaseReader, FlightBlenderDatabaseWriter
from conformance_monitoring_operations.data_definitions import PolygonAltitude
from flight_declaration_operations.utils import OperationalIntentsConverter
from flight_feed_operations import flight_stream_helper
from scd_operations.dss_scd_helper import OperationalIntentReferenceHelper, SCDOperations
from scd_operations.scd_data_definitions import (
    OperationalIntentReferenceDSSResponse,
    Time,
    Volume4D,
)

load_dotenv(find_dotenv())
ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)

logger = logging.getLogger("django")


class Command(BaseCommand):
    help = "This command takes in a flight declaration: and A) declares it as non-conforming, B) creates off-nominal volumes C) Updates the DSS with the new status D) Notifies Peer USS "

    def add_arguments(self, parser):
        parser.add_argument(
            "-f",
            "--flight_declaration_id",
            dest="flight_declaration_id",
            metavar="ID of the flight declaration",
            help="Specify the ID of Flight Declaration",
        )

        parser.add_argument(
            "-d",
            "--dry_run",
            dest="dry_run",
            metavar="Set if this is a dry run",
            default="1",
            help="Set if it is a dry run",
        )

    def handle(self, *args, **options):
        # This command declares an operation as non-conforming and updates the state to the DSS (and notifies subscribers)
        my_database_reader = FlightBlenderDatabaseReader()
        my_database_writer = FlightBlenderDatabaseWriter()
        my_operational_intents_helper = OperationalIntentReferenceHelper()
        my_scd_dss_helper = SCDOperations()
        dry_run = options["dry_run"]

        dry_run = 1 if dry_run == "1" else 0

        # Set new state as non-conforming
        new_state_int = 3
        new_state_str = OPERATION_STATES[new_state_int][1]
        try:
            flight_declaration_id = options["flight_declaration_id"]
        except Exception as e:
            raise CommandError("Incomplete command, Flight Declaration ID not provided %s" % e)
        # Get the flight declaration
        flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)
        if not flight_declaration:
            raise CommandError(f"Flight Declaration with ID {flight_declaration_id} does not exist")

        flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_id(
            flight_declaration_id=flight_declaration_id
        )

        stored_operational_intent = my_operational_intents_helper.parse_stored_operational_intent_details(operation_id=flight_declaration_id)

        try:
            flight_declaration_id = options["flight_declaration_id"]
        except Exception as e:
            raise CommandError("Incomplete command, Flight Declaration ID not provided %s" % e)

        flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)

        if not flight_declaration:
            raise CommandError(f"Flight Declaration with ID {flight_declaration_id} does not exist")

        current_state = flight_declaration.state
        current_state_str = OPERATION_STATES[current_state][1]

        reference_full = stored_operational_intent.success_response.operational_intent_reference
        dss_response_subscribers = stored_operational_intent.success_response.subscribers
        details_full = stored_operational_intent.operational_intent_details
        # Load existing opint details

        stored_operational_intent_id = reference_full.id
        stored_manager = reference_full.manager
        stored_uss_availability = reference_full.uss_availability
        stored_version = reference_full.version
        stored_state = reference_full.state
        stored_ovn = reference_full.ovn
        stored_uss_base_url = reference_full.uss_base_url
        stored_subscription_id = reference_full.subscription_id

        stored_time_start = Time(
            format="RFC3339",
            value=reference_full.time_start,
        )
        stored_time_end = Time(
            format="RFC3339",
            value=reference_full.time_end,
        )

        stored_volumes = details_full.volumes
        stored_priority = details_full.priority
        stored_off_nominal_volumes = details_full.off_nominal_volumes
        logger.debug(stored_priority)
        logger.debug(stored_off_nominal_volumes)
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
        if not dry_run:
            operational_update_response = my_scd_dss_helper.update_specified_operational_intent_reference(
                subscription_id=stored_subscription_id,
                operational_intent_ref_id=str(reference.id),
                extents=stored_volumes,
                new_state=new_state_str,
                ovn=reference.ovn,
                deconfliction_check=False,
                priority=0,
                current_state=current_state_str,
            )

            ## Update / expand volume

            obs_helper = flight_stream_helper.ObservationReadOperations()

            # Get the last observation of the flight telemetry
            obs_helper = flight_stream_helper.ObservationReadOperations()
            latest_telemetry_data = obs_helper.get_latest_flight_observation_by_flight_declaration_id(flight_declaration_id=flight_declaration_id)
            # Get the latest telemetry

            if not latest_telemetry_data:
                logger.error(f"No telemetry data found for operation {flight_declaration_id}")
                return

            lat_dd = latest_telemetry_data.latitude_dd
            lon_dd = latest_telemetry_data.longitude_dd

            max_altitude = latest_telemetry_data.altitude_mm + 10
            min_altitude = latest_telemetry_data.altitude_mm - 10
            my_op_int_converter = OperationalIntentsConverter()
            new_volume_4d = my_op_int_converter.buffer_point_to_volume4d(
                lat=lat_dd,
                lng=lon_dd,
                start_datetime=flight_declaration.start_datetime.isoformat(),
                end_datetime=flight_declaration.end_datetime.isoformat(),
                min_altitude=min_altitude,
                max_altitude=max_altitude,
            )
            logger.debug(new_volume_4d)

            operational_update_response = my_scd_dss_helper.update_specified_operational_intent_reference(
                subscription_id=stored_subscription_id,
                operational_intent_ref_id=reference.id,
                extents=stored_volumes,
                ovn=reference.ovn,
                deconfliction_check=True,
                new_state=new_state_str,
                current_state=current_state_str,
            )

            if operational_update_response.status == 200:
                logger.info(
                    "Successfully updated operational intent status for {operational_intent_id} on the DSS".format(
                        operational_intent_id=stored_operational_intent_id
                    )
                )
            else:
                logger.info("Error in updating operational intent on the DSS")

        else:
            logger.info("Dry run, not submitting to the DSS")
