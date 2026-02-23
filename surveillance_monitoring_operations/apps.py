from django.apps import AppConfig


class SurveillanceMonitoringOperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "surveillance_monitoring_operations"

    def ready(self):
        import surveillance_monitoring_operations.custom_signals  # noqa: F401
