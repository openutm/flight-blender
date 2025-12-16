from os import environ as env

from django.core.management.base import BaseCommand

# Configure logging
from dotenv import find_dotenv, load_dotenv
from loguru import logger

from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from scd_operations import dss_scd_helper

load_dotenv(find_dotenv())


class Command(BaseCommand):
    help = "This command deletes all flight operations in the Flight Blender database and also clears the DSS if available"

    def add_arguments(self, parser):
        """
        Add command line arguments to the parser.
        """
        parser.add_argument(
            "-d",
            "--dry_run",
            dest="dry_run",
            metavar="Set if this is a dry run",
            default="1",
            help="Set if it is a dry run",
        )

        parser.add_argument(
            "-s",
            "--dss",
            dest="dss",
            metavar="Specify if the operational intents should also be removed from the DSS",
            default=1,
            type=int,
            help="Specify if the operational intents should also be removed from the DSS",
        )

    def handle(self, *args, **options):
        """
        Handle the command execution.
        """
        dry_run = options["dry_run"]
        clear_dss = options["dss"]

        USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))

        dry_run = 1 if dry_run == "1" else 0
        my_database_reader = FlightBlenderDatabaseReader()
        my_database_writer = FlightBlenderDatabaseWriter()
        all_operations = my_database_reader.get_all_flight_declarations()
        my_scd_dss_helper = dss_scd_helper.SCDOperations()
        for flight_declaration in all_operations:
            f_a = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_obj(flight_declaration=flight_declaration)
            if dry_run:
                logger.info("Dry Run : Deleting operation %s", flight_declaration.id)
            else:
                logger.info("Deleting operation %s...", flight_declaration.id)

                if clear_dss and f_a:
                    operational_intent_id = str(f_a.id)
                    stored_ovn = f_a.ovn

                    logger.info("Clearing operational intent id %s in the DSS...", operational_intent_id)
                    logger.info("Stored OVN: %s", stored_ovn)
                    logger.info("Flight declaration id: %s", flight_declaration.id)
                    if stored_ovn and USSP_NETWORK_ENABLED:
                        my_scd_dss_helper.delete_operational_intent(ovn=stored_ovn, dss_operational_intent_ref_id=operational_intent_id)

                    # Remove the conformance monitoring periodic job
                    conformance_monitoring_job = my_database_reader.get_conformance_monitoring_task(flight_declaration=flight_declaration)
                    if conformance_monitoring_job:
                        my_database_writer.remove_conformance_monitoring_periodic_task(conformance_monitoring_task=conformance_monitoring_job)

                flight_declaration.delete()

        # Clear out Redis database
        logger.info("Clearing stored operational intents...")
        my_database_writer.clear_stored_operational_intents()
