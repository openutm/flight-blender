from django.contrib import admin

from .models import (
    SurveillanceHeartbeatEvent,
    SurveillanceSensor,
    SurveillanceSensorFailureNotification,
    SurveillanceSensorHealth,
    SurveillanceSensorMaintenance,
    SurveillanceSensortHealthTracking,
    SurveillanceTrackEvent,
)

admin.site.register(SurveillanceSensor)
admin.site.register(SurveillanceSensorHealth)
admin.site.register(SurveillanceSensorMaintenance)
admin.site.register(SurveillanceSensortHealthTracking)
admin.site.register(SurveillanceHeartbeatEvent)
admin.site.register(SurveillanceTrackEvent)
admin.site.register(SurveillanceSensorFailureNotification)
