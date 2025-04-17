import json
import logging
from os import environ as env

from django.core.management.base import BaseCommand, CommandError
from dotenv import find_dotenv, load_dotenv

from auth_helper.common import get_redis
from common.data_definitions import OPERATION_STATES
from common.database_operations import FlightBlenderDatabaseReader
from scd_operations.dss_scd_helper import SCDOperations
from scd_operations.scd_data_definitions import (
    OperationalIntentReferenceDSSResponse,
    Time,
)

load_dotenv(find_dotenv())
ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)
logger = logging.getLogger("django")


class Command(BaseCommand):
    help = "This command clears the operation in the DSS after the state has been set to ended."

    def add_arguments(self, parser):
        parser.add_argument(
            "-o",
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
        dry_run = options["dry_run"]
        dry_run = 1 if dry_run == "1" else 0

        new_state = OPERATION_STATES[2][1]

        try:
            flight_declaration_id = options["flight_declaration_id"]
        except Exception as e:
            raise CommandError("Incomplete command, Flight Declaration ID not provided %s" % e)

        # Get the flight declaration

        my_database_reader = FlightBlenderDatabaseReader()

        flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)
        if not flight_declaration:
            raise CommandError(
                "Flight Declaration with ID {flight_declaration_id} does not exist".format(flight_declaration_id=flight_declaration_id)
            )

        current_state = flight_declaration.state
        current_state_str = OPERATION_STATES[current_state][1]
        my_scd_dss_helper = SCDOperations()
        flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_id(flight_declaration_id=flight_declaration_id)

        operational_intent_id = flight_operational_intent_reference.id

        r = get_redis()

        flight_opint = "flight_opint." + str(flight_declaration_id)

        if r.exists(flight_opint):
            op_int_details_raw = r.get(flight_opint)
            op_int_details = json.loads(op_int_details_raw)

            reference_full = op_int_details["success_response"]["operational_intent_reference"]
            dss_response_subscribers = op_int_details["success_response"]["subscribers"]
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

            stored_volumes = details_full["volumes"]
            stored_priority = details_full["priority"]
            stored_off_nominal_volumes = details_full["off_nominal_volumes"]
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
                flight_blender_base_url = env.get("FLIGHTBLENDER_FQDN", "http://localhost:8000")
                for subscriber in dss_response_subscribers:
                    subscriptions = subscriber["subscriptions"]
                    uss_base_url = subscriber["uss_base_url"]
                    if flight_blender_base_url == uss_base_url:
                        for s in subscriptions:
                            subscription_id = s["subscription_id"]
                            break
                # Create a new subscription to the airspace
                operational_update_response = my_scd_dss_helper.update_specified_operational_intent_reference(
                    subscription_id=subscription_id,
                    operational_intent_ref_id=reference.id,
                    extents=stored_volumes,
                    new_state=str(new_state),
                    ovn=reference.ovn,
                    deconfliction_check=True,
                    priority=0,
                    current_state=current_state_str,
                )

                if operational_update_response.status == 200:
                    logger.info(
                        "Successfully updated operational intent status for {operational_intent_id} on the DSS".format(
                            operational_intent_id=operational_intent_id
                        )
                    )
                else:
                    logger.info("Error in updating operational intent on the DSS")

            else:
                logger.info("Dry run, not submitting to the DSS")

        else:
            logger.info("Operational intent with {flight_declaration_id} does not exist...".format(flight_declaration_id=flight_declaration_id))
