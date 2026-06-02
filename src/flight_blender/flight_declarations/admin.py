from django.contrib import admin

from .models import FlightDeclaration, FlightOperationalIntentReference

# Register your models here.

admin.site.register(FlightDeclaration)
admin.site.register(FlightOperationalIntentReference)
