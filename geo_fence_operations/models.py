import uuid
from datetime import datetime

from django.db import models
from django.utils.translation import gettext_lazy as _


class GeoFence(models.Model):
    """A model for Geofence storage in Flight Blender"""

    ALTITUDE_REF = (
        (0, _("WGS84")),
        (1, _("AGL")),
        (2, _("MSL")),
    )

    STATUS_CODES = (
        (0, _("Activating")),
        (1, _("Ready")),
        (3, _("Deactivating")),
        (4, _("Unsupported")),
        (5, _("Rejected")),
        (6, _("Error")),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    raw_geo_fence = models.TextField(blank=True, null=True, help_text="Set a GeoJSON as a GeoFence")

    geozone = models.TextField(help_text="Set a ED-269 Compliant GeoZone", blank=True, null=True)

    upper_limit = models.DecimalField(max_digits=6, decimal_places=2)
    lower_limit = models.DecimalField(max_digits=6, decimal_places=2)

    altitude_ref = models.IntegerField(choices=ALTITUDE_REF, default=0)

    name = models.CharField(max_length=50)
    bounds = models.CharField(max_length=140)

    status = models.IntegerField(choices=STATUS_CODES, default=0)
    message = models.CharField(max_length=140, help_text="Set the status regarding the availability of the dataset", blank=True, null=True)

    is_test_dataset = models.BooleanField(
        default=False,
        help_text="Specify if this is a test dataset that is used in the USS Qualifier tests",
    )

    start_datetime = models.DateTimeField(default=datetime.now)
    end_datetime = models.DateTimeField(default=datetime.now)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __unicode__(self):
        return self.name

    def __str__(self):
        return self.name
