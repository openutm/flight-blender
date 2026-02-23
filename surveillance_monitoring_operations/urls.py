from django.urls import path

from . import views as surveillance_monitoring_views

urlpatterns = [
    path("health/", surveillance_monitoring_views.surveillance_health, name="health"),
    path(
        "start_stop_surveillance_heartbeat_track/<uuid:session_id>",
        surveillance_monitoring_views.start_stop_surveillance_heartbeat_track,
        name="start_stop_surveillance_heartbeat_track",
    ),
    path(
        "list_surveillance_sensors/",
        surveillance_monitoring_views.list_surveillance_sensors,
        name="list_surveillance_sensors",
    ),
    path(
        "service_metrics/",
        surveillance_monitoring_views.service_metrics,
        name="service_metrics",
    ),
    path(
        "update_sensor_health/<uuid:sensor_id>/",
        surveillance_monitoring_views.update_sensor_health,
        name="update_sensor_health",
    ),
    path(
        "sensor_failure_notifications/",
        surveillance_monitoring_views.list_sensor_failure_notifications,
        name="sensor_failure_notifications",
    ),
]
