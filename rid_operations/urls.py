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

from django.urls import path

from . import views as rid_views

urlpatterns = [
    # Flight Spotlight specific views
    path("create_dss_subscription", rid_views.create_dss_subscription),
    # USSP RID views
    ### This is the call back url to DSS
    path(
        "uss/identification_service_areas/<uuid:isa_id>",
        rid_views.dss_isa_callback,
    ),
    # Get RID data (PULL)
    path("get_rid_data/<uuid:subscription_id>", rid_views.get_rid_data),
    # RID Qualifier data: Observation, for more information see: https://github.com/interuss/dss/tree/master/interfaces/automated-testing/rid
    path("display_data", rid_views.get_display_data),
    path("display_data/<uuid:flight_id>", rid_views.get_flight_data),
    # RID Qualifier data: Injection views
    path("tests/<uuid:test_id>", rid_views.create_test),
    path("tests/<uuid:test_id>/<str:version>", rid_views.delete_test),
    path("user_notifications", rid_views.user_notifications),
    path("capabilities", rid_views.get_rid_capabilities),
]
