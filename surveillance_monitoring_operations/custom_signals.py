import django.dispatch
from django.dispatch import receiver
from loguru import logger

surveillance_sensor_failure_signal = django.dispatch.Signal()


@receiver(surveillance_sensor_failure_signal)
def process_sensor_status_change(sender, **kwargs):
    """
    Creates a SurveillanceSensorFailureNotification DB record whenever a sensor's
    health status changes. Fired by FlightBlenderDatabaseWriter.update_sensor_health_status().

    Expected kwargs:
        sensor_id (str): UUID of the SurveillanceSensor
        previous_status (str): Status before the change
        new_status (str): Status after the change
        recovery_type (str | None): "automatic" or "manual" for operational recoveries; None otherwise
    """
    from surveillance_monitoring_operations.models import SurveillanceSensor, SurveillanceSensorFailureNotification

    sensor_id = kwargs["sensor_id"]
    previous_status = kwargs["previous_status"]
    new_status = kwargs["new_status"]
    recovery_type = kwargs.get("recovery_type")

    try:
        sensor = SurveillanceSensor.objects.get(id=sensor_id)
    except SurveillanceSensor.DoesNotExist:
        logger.error(f"surveillance signal: sensor {sensor_id} not found, skipping notification")
        return

    if new_status in ("degraded", "outage"):
        message = f"Sensor '{sensor.sensor_identifier}' entered {new_status} state (was {previous_status})"
        logger.warning(message)
    else:
        recovery_label = f" [{recovery_type} recovery]" if recovery_type else ""
        message = f"Sensor '{sensor.sensor_identifier}' recovered to {new_status} (was {previous_status}){recovery_label}"
        logger.info(message)

    SurveillanceSensorFailureNotification.objects.create(
        sensor=sensor,
        previous_status=previous_status,
        new_status=new_status,
        recovery_type=recovery_type,
        message=message,
    )
