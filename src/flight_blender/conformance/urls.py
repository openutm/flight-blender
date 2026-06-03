from django.urls import path

from . import views as conformance_monitoring_views

urlpatterns = [
    path(
        "conformance_record_summary",
        conformance_monitoring_views.get_conformance_record_summary,
        name="conformance_record_summary",
    ),
    path(
        "conformance_status",
        conformance_monitoring_views.conformance_status,
        name="conformance_status",
    ),
    path(
        "get_conformance_records",
        conformance_monitoring_views.get_conformance_records,
        name="all_conformance_records",
    ),
]
