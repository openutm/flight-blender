from uuid import UUID

from loguru import logger

from flight_blender.common.database_operations import FlightBlenderDatabaseReader
from flight_blender.common.dispatch import Signal, receiver

surveillance_sensor_failure_signal = Signal()


@receiver(surveillance_sensor_failure_signal)
def process_sensor_status_change(sender, **kwargs):
    sensor_id = kwargs["sensor_id"]
    previous_status = kwargs["previous_status"]
    new_status = kwargs["new_status"]
    recovery_type = kwargs.get("recovery_type")
    my_database_reader = FlightBlenderDatabaseReader()

    sensor = my_database_reader.get_surveillance_sensor_by_id(sensor_id=UUID(sensor_id))
    if not sensor:
        logger.error(f"surveillance signal: sensor {sensor_id} not found, skipping notification")
        return

    if new_status in ("degraded", "outage"):
        message = f"Sensor '{sensor.sensor_identifier}' entered {new_status} state (was {previous_status})"
        logger.warning(message)
    else:
        recovery_label = f" [{recovery_type} recovery]" if recovery_type else ""
        message = f"Sensor '{sensor.sensor_identifier}' recovered to {new_status} (was {previous_status}){recovery_label}"
        logger.info(message)

    from flight_blender.infrastructure.database.models.surveillance import SurveillanceSensorFailureNotificationORM  # noqa: PLC0415
    from flight_blender.infrastructure.database.session import session_scope  # noqa: PLC0415

    with session_scope() as db:
        obj = SurveillanceSensorFailureNotificationORM(
            sensor_id=UUID(sensor_id),
            previous_status=previous_status,
            new_status=new_status,
            recovery_type=recovery_type,
            message=message,
        )
        db.add(obj)
