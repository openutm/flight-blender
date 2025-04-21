from django.contrib import admin

from .models import FlightOperationalIntentReference, FlightDeclaration

# Register your models here.

admin.site.register(FlightDeclaration)
admin.site.register(FlightOperationalIntentReference)
