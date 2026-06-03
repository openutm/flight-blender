from django.apps import AppConfig


class SurveillanceMonitoringOperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "flight_blender.surveillance"
    label = "surveillance_monitoring_operations"

    def ready(self):
        import flight_blender.surveillance.custom_signals  # noqa: F401
