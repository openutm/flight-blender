import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

from common.data_definitions import FLIGHT_OBSERVATION_TRAFFIC_SOURCE


def get_thirty_minutes_from_now():
    return timezone.now() + timedelta(minutes=30)


class SurveillanceSession(models.Model):
    """
    A Django model representing a surveillance session.
    This model stores information about a surveillance session, including its unique identifier,
    validity period, and timestamps for creation and updates.
    Attributes:
        id (UUIDField): The primary key, a unique UUID generated automatically.
        valid_until (DateTimeField): The expiration date and time of the session, defaulting to 30 minutes from creation.
        created_at (DateTimeField): The timestamp when the session was created, set automatically on creation.
        updated_at (DateTimeField): The timestamp when the session was last updated, set automatically on save.
    Methods:
        __str__(): Returns the string representation of the session, which is its ID.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    valid_until = models.DateTimeField(default=get_thirty_minutes_from_now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.id)


class SurveillanceSensor(models.Model):
    """
    Model representing a surveillance sensor used in flight observation and traffic monitoring.

    Attributes:
        id (UUIDField): Unique identifier for the sensor, automatically generated.
        sensor_type (IntegerField): Type of the sensor, chosen from FLIGHT_OBSERVATION_TRAFFIC_SOURCE options, defaults to 12.
        sensor_identifier (CharField): Unique string describing the sensor (e.g., serial number or location), max length 100.
        is_active (BooleanField): Indicates if the sensor is currently active, defaults to True.
        created_at (DateTimeField): Timestamp when the sensor was created, set automatically.
        updated_at (DateTimeField): Timestamp when the sensor was last updated, set automatically.

    Methods:
        __str__(): Returns a string representation of the sensor as '{sensor_type} - {sensor_identifier}'.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sensor_type = models.IntegerField(choices=FLIGHT_OBSERVATION_TRAFFIC_SOURCE, default=12)
    sensor_identifier = models.CharField(
        max_length=256,
        unique=True,
        help_text="Describe the sensor, e.g., serial number or detailed location",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def sensor_type_display(self):
        return dict(FLIGHT_OBSERVATION_TRAFFIC_SOURCE).get(self.sensor_type, str(self.sensor_type))

    def __str__(self):
        return f"{self.sensor_type} - {self.sensor_identifier}"
