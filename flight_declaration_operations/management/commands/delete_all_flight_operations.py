import json

from django.core.management.base import BaseCommand

from auth_helper.common import RedisHelper, get_redis
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from scd_operations import dss_scd_helper


class Command(BaseCommand):
    help = "This command deletes all flight operations in the Flight Blender database and also clears the DSS if available"

    def add_arguments(self, parser):
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
            help="Set if it is a dry run",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        clear_dss = options["dss"]

        r = get_redis()
        dry_run = 1 if dry_run == "1" else 0
        my_database_reader = FlightBlenderDatabaseReader()
        my_database_writer = FlightBlenderDatabaseWriter()
        all_operations = my_database_reader.get_all_flight_declarations()
        for o in all_operations:
            f_a = my_database_reader.get_flight_authorization_by_flight_declaration_obj(flight_declaration=o)
            if dry_run:
                print("Dry Run : Deleting operation %s" % o.id)
            else:
                print("Deleting operation %s..." % o.id)
                if clear_dss:
                    if f_a:
                        dss_op_int_id = f_a.dss_operational_intent_id
                        print("Clearing operational intent id  %s in the DSS..." % dss_op_int_id)
                        my_scd_dss_helper = dss_scd_helper.SCDOperations()
                        # Get the OVN
                        op_int_details_key = "flight_opint." + str(f_a.declaration_id)
                        op_int_details_raw = r.get(op_int_details_key)
                        if op_int_details_raw:
                            op_int_details = json.loads(op_int_details_raw)
                            ovn = op_int_details["success_response"]["operational_intent_reference"]["ovn"]
                            my_scd_dss_helper.delete_operational_intent(ovn=ovn, dss_operational_intent_ref_id=dss_op_int_id)

                        # Remove the conformance monitoring periodic job
                        conformance_monitoring_job = my_database_reader.get_conformance_monitoring_task(flight_declaration=o)
                        if conformance_monitoring_job:
                            my_database_writer.remove_conformance_monitoring_periodic_task(conformance_monitoring_task=conformance_monitoring_job)

                o.delete()

        # Clear out Redis database
        print("Clearing stored operational intents...")
        redis = RedisHelper()
        redis.delete_all_opints()
