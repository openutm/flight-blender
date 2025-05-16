import logging
from os import environ as env

from django.core.management.base import BaseCommand

# Configure logging
from dotenv import find_dotenv, load_dotenv

from constraint_operations.models import ConstraintDetail

logger = logging.getLogger("django")
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

        dry_run = 1 if dry_run == "1" else 0
        all_constraint_details = ConstraintDetail.objects.all()
        for constraint_detail in all_constraint_details:
            if dry_run:
                logger.info("Dry Run : Deleting constraint detail %s", constraint_detail.id)
            else:
                constraint_detail.delete()
