"""flight_blender URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

# from django.contrib import admin
from django.urls import path

from . import views as uss_operations_views

urlpatterns = [
    # USS Details Urls
    # This end point is used when op-intent details is used by the USS: https://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/astm-utm/Protocol/cb7cf962d3a0c01b5ab12502f5f54789624977bf/utm.yaml#section/Authentication
    # path('v1/operational_intents', uss_operations_views.uss_update_opint_details),
    path("v1/reports", uss_operations_views.peer_uss_report_notification),
    path("v1/operational_intents/<uuid:opint_id>", uss_operations_views.operational_intent_details),
    path(
        "v1/operational_intents/<uuid:opint_id>/telemetry",
        uss_operations_views.USSOpIntDetailTelemetry,
    ),
    path("v1/operational_intents", uss_operations_views.uss_update_opint_details),

    path("v1/constraints/<uuid:constraint_id>", uss_operations_views.uss_constraint_details),
    path("v1/constraints", uss_operations_views.uss_update_constraint_details),

    # end points for remote id
    path("flights/<str:flight_id>/details", uss_operations_views.get_uss_flight_details),
    path("flights", uss_operations_views.get_uss_flights),
]
