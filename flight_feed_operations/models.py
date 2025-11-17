import uuid

from django.db import models

from common.data_definitions import FLIGHT_OBSERVATION_TRAFFIC_SOURCE

# Create your models here.


class SignedTelmetryPublicKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key_id = models.TextField(help_text="Specify the Key ID")
    url = models.URLField(help_text="Enter the JWK / JWKS URL of the public key")
    is_active = models.BooleanField(
        default=True,
        help_text="Specify if the key is active, only active keys will be validated against in the signed telemetry feeds",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Key : " + self.url


class FlightObservation(models.Model):
    """
    Model representing a flight stream observation.
    Attributes:
        id (UUIDField): Primary key for the observation, auto-generated UUID.
        session_id (UUIDField): Session ID for the observation, when an observation is associated with a flight declaration then the Flight Declaration ID is used here, when the observation is associated with a sensor, the sensor ID is used here, else it can be a random UUID.
        latitude_dd (FloatField): Latitude of the observation in decimal degrees.
        longitude_dd (FloatField): Longitude of the observation in decimal degrees.
        altitude_mm (FloatField): Altitude of the observation in millimeters.
        timestamp (DateTimeField): Timestamp of the observation.
        traffic_source (IntegerField): Traffic source of the observation.
        source_type (IntegerField): Source type of the observation.
        icao_address (TextField): ICAO address of the observation.
        metadata (TextField): Raw data for the RID stream.
        created_at (DateTimeField): Timestamp when the observation was created.
        updated_at (DateTimeField): Timestamp when the observation was last updated.
    Schema:
        ObservationSchema: Schema used to describe the observation data.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_id = models.UUIDField(help_text="Session ID for the stream", blank=True, null=True)
    latitude_dd = models.FloatField(help_text="Latitude of the observation")
    longitude_dd = models.FloatField(help_text="Longitude of the observation")
    altitude_mm = models.FloatField(help_text="Altitude of the observation")
    traffic_source = models.IntegerField(choices=FLIGHT_OBSERVATION_TRAFFIC_SOURCE, help_text="Source of the observation")
    source_type = models.IntegerField(help_text="Source type of the observation")
    icao_address = models.TextField(help_text="ICAO address of the observation")

    metadata = models.TextField(help_text="Raw data for the RID stream")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
