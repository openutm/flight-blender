# my_app/apps.py

from django.apps import AppConfig


class MyAppConfig(AppConfig):
    name = "flight_feed_operations"

    def ready(self):
        pass
