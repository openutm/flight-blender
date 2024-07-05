from django.urls import path, re_path

from . import views as scd_auth_views

urlpatterns = [
    # SCD Test URLS
    path("v1/status", scd_auth_views.scd_test_status),
    path("v1/capabilities", scd_auth_views.scd_test_capabilities),
    path("flight_planning/flight_plans/<uuid:flight_plan_id>", scd_auth_views.upsert_close_flight_plan),
    re_path("flight_planning/clear_area_requests", scd_auth_views.flight_planning_clear_area_request),
    re_path("flight_planning/status", scd_auth_views.flight_planning_status),
]
