from django.contrib import admin

from .models import SurveillanceSensor, SurveillanceSensorHealth, SurveillanceSensorMaintenance

admin.site.register(SurveillanceSensor)
admin.site.register(SurveillanceSensorHealth)
admin.site.register(SurveillanceSensorMaintenance)
