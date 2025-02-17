from django.core.management.base import BaseCommand

from flight_feed_operations import flight_stream_helper


class Command(BaseCommand):
    help = "Create a stream and consumer group once"

    def handle(self, *args, **options):
        my_stream_ops = flight_stream_helper.StreamHelperOps()

        my_stream_ops.create_pull_cg()