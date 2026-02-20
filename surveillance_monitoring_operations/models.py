import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

from common.data_definitions import FLIGHT_OBSERVATION_TRAFFIC_SOURCE, SURVEILLANCE_SENSOR_HEALTH_CHOICES, SURVEILLANCE_SENSOR_MAINTENANCE_CHOICES

RECOVERY_TYPE_CHOICES = [
    ("automatic", "Automatic"),
    ("manual", "Manual"),
]


def get_thirty_minutes_from_now():
    return timezone.now() + timedelta(minutes=30)


def get_surveillance_sensor_health_choices():
    return {i: i for i in SURVEILLANCE_SENSOR_HEALTH_CHOICES}


def get_surveillance_sensor_maintenance_choices():
    return {i: i for i in SURVEILLANCE_SENSOR_MAINTENANCE_CHOICES}


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
    refresh_rate_seconds = models.FloatField(default=1.0)
    is_active = models.BooleanField(default=True)
    horizontal_accuracy_m = models.FloatField(default=5.0, help_text="95th percentile horizontal accuracy in meters")
    vertical_accuracy_m = models.FloatField(default=5.0, help_text="95th percentile vertical accuracy in meters")
    expected_latency_ms = models.IntegerField(default=150, help_text="Expected average data delivery latency in milliseconds")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def sensor_type_display(self):
        return dict(FLIGHT_OBSERVATION_TRAFFIC_SOURCE).get(self.sensor_type, str(self.sensor_type))

    def __str__(self):
        return f"{self.sensor_type} - {self.sensor_identifier}"


class SurveillanceSensorHealth(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sensor = models.OneToOneField(SurveillanceSensor, on_delete=models.CASCADE, related_name="health_records")
    status = models.CharField(max_length=12, choices=SURVEILLANCE_SENSOR_HEALTH_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)


class SurveillanceSensortHealthTracking(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sensor = models.ForeignKey(SurveillanceSensor, on_delete=models.CASCADE, related_name="health_tracking_records")
    status = models.CharField(max_length=12, choices=SURVEILLANCE_SENSOR_HEALTH_CHOICES)
    recorded_at = models.DateTimeField(auto_now_add=True)
    recovery_type = models.CharField(
        max_length=12,
        choices=RECOVERY_TYPE_CHOICES,
        null=True,
        blank=True,
        help_text="Set when status='operational'. Null for failure transitions.",
    )


class SurveillanceSensorMaintenance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sensor = models.OneToOneField(SurveillanceSensor, on_delete=models.CASCADE, related_name="maintenance_records")
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    planned_or_unplanned = models.CharField(max_length=12, choices=SURVEILLANCE_SENSOR_MAINTENANCE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class SurveillanceHeartbeatEvent(models.Model):
    """Records each heartbeat dispatch for heartbeat rate and delivery probability metrics."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(SurveillanceSession, on_delete=models.CASCADE, related_name="heartbeat_events")
    dispatched_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expected_at = models.DateTimeField(help_text="Scheduled dispatch time based on 1Hz cadence")
    delivered_on_time = models.BooleanField(default=True, help_text="True if dispatch succeeded within the acceptable latency window")

    def __str__(self):
        return f"HeartbeatEvent session={self.session} at {self.dispatched_at}"


class SurveillanceTrackEvent(models.Model):
    """Records each track-task execution outcome for track update probability metrics."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(SurveillanceSession, on_delete=models.CASCADE, related_name="track_events")
    dispatched_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expected_at = models.DateTimeField(help_text="Scheduled dispatch time based on 1Hz cadence")
    had_active_tracks = models.BooleanField(default=False, help_text="True if the fuser produced at least one track message this tick")

    def __str__(self):
        return f"TrackEvent session={self.session} at {self.dispatched_at}"


class SurveillanceSensorFailureNotification(models.Model):
    """Persists sensor failure and recovery events for audit and notification purposes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sensor = models.ForeignKey(SurveillanceSensor, on_delete=models.CASCADE, related_name="failure_notifications")
    previous_status = models.CharField(max_length=12)
    new_status = models.CharField(max_length=12)
    recovery_type = models.CharField(max_length=12, null=True, blank=True, help_text="Set only for operational recoveries")
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"FailureNotification sensor={self.sensor} {self.previous_status}->{self.new_status}"
